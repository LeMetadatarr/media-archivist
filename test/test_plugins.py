# SPDX-License-Identifier: Apache-2.0
"""Providers loaded through the ``media_archivist.providers`` entry points."""
from __future__ import annotations

import logging
import time
from importlib.metadata import EntryPoint
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_exports import client, env, export, thumbs, wait_done  # noqa: F401

from media_archivist import export_hooks, indexdb, links, plugins, strm
from media_archivist import subscriptions as subs
from media_archivist.models.canonical import stable_id
from media_archivist.models.raw import Source
from media_archivist.server.app import create_app
from media_archivist.settings import Settings
from media_archivist.storage import EnvelopeJsonStorage

FIXTURES = Path(__file__).parent / "fixtures"
MODULE = "example_provider"


def ep(name, attr, module=MODULE):
    return EntryPoint(name, f"{module}:{attr}", plugins.GROUP)


GOOD = [ep("example", "ExampleProvider"), ep("adultex", "AdultExampleProvider")]


@pytest.fixture(autouse=True)
def _example_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(FIXTURES))
    yield
    plugins.reload()


@pytest.fixture
def loaded():
    return plugins.reload(GOOD)


def enable(db, *names, adult=()):
    settings = Settings(indexdb.open_index(db))
    settings.update({"enabled_providers": [*plugins.BUILTIN_NAMES, *names],
                     "adult_export_providers": list(adult)})


def test_built_in_providers_register_through_installed_entry_points():
    declared = {e.name: e.value for e in plugins._installed()}
    assert declared == plugins.BUILTIN
    reg = plugins.reload()
    assert set(reg.providers) == set(plugins.BUILTIN) and not reg.errors
    assert all(p.builtin and not p.adult for p in reg.providers.values())


def test_an_installed_distribution_is_discovered(tmp_path, monkeypatch):
    dist = tmp_path / "example_plugin-0.1.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text("Metadata-Version: 2.1\nName: example-plugin\nVersion: 0.1\n")
    (dist / "entry_points.txt").write_text(
        f"[{plugins.GROUP}]\nexample = {MODULE}:ExampleProvider\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    reg = plugins.reload()
    assert reg.providers["example"].builtin is False
    assert reg.providers["example"].api_version == plugins.API_VERSION


def test_a_failing_plugin_is_reported_and_the_others_load():
    reg = plugins.reload([
        *GOOD,
        ep("oldapi", "OldApiProvider"),
        ep("noarchive", "NoArchiveProvider"),
        ep("missing", "Anything", module="no_such_module_anywhere"),
        ep("youtube", "ExampleProvider"),
        ep("same_source", "ExampleProvider"),
    ])
    assert {"example", "adultex", "youtube"} <= set(reg.providers)
    assert reg.providers["youtube"].builtin
    assert "API_VERSION is 0" in reg.errors["oldapi"]
    assert "no archive(url)" in reg.errors["noarchive"]
    assert "ModuleNotFoundError" in reg.errors["missing"]
    assert "already registered" in reg.errors[f"youtube [{MODULE}:ExampleProvider]"]
    assert "already claimed by 'example'" in reg.errors["same_source"]


def test_unreadable_entry_points_leave_the_built_ins_working(monkeypatch):
    def boom():
        raise OSError("metadata unreadable")

    monkeypatch.setattr(plugins, "_installed", boom)
    reg = plugins.reload()
    assert set(reg.providers) == set(plugins.BUILTIN)
    assert "metadata unreadable" in reg.errors["<entry points>"]


def test_installed_is_not_enabled(tmp_path, loaded):
    db = str(tmp_path / "db.json")
    with pytest.raises(ValueError, match="installed but not enabled"):
        subs.add_subscription(db, "https://example.test/feed", backend="example")
    assert subs.infer_backend("https://example.test/feed", db_path=db) is None
    assert subs.infer_backend("https://example.test/feed") == "example"
    assert subs.infer_backend("https://www.youtube.com/@x", db_path=db) == "youtube"

    indexdb.open_index(db)
    enable(db, "example")
    sub = subs.add_subscription(db, "https://example.test/feed")
    assert sub.backend == "example"
    result = subs.sync_subscription(db, sub)
    assert result.ok and result.rows_added == 1

    enable(db)
    result = subs.sync_subscription(db, sub)
    assert not result.ok and "not enabled" in result.error


def test_a_plugin_that_raises_while_syncing_does_not_stop_the_sync(tmp_path):
    plugins.reload([ep("exploding", "ExplodingProvider")])
    db = str(tmp_path / "db.json")
    indexdb.open_index(db)
    enable(db, "exploding")
    sub = subs.add_subscription(db, "https://example.test/x", backend="exploding")
    result = subs.sync_subscription(db, sub)
    assert not result.ok and "cannot start" in result.error


def test_unknown_and_failed_providers_are_refused_by_name(tmp_path):
    plugins.reload([ep("oldapi", "OldApiProvider")])
    db = str(tmp_path / "db.json")
    with pytest.raises(ValueError, match="failed to load: ValueError: API_VERSION is 0"):
        subs.add_subscription(db, "https://example.test/x", backend="oldapi")
    with pytest.raises(ValueError, match="unknown backend: 'nothing'"):
        subs.add_subscription(db, "https://example.test/x", backend="nothing")


def test_the_providers_route_and_the_settings_that_enable(env, loaded):
    plugins.reload([*GOOD, ep("oldapi", "OldApiProvider")])
    with client(env) as c:
        body = c.get("/api/v1/providers").json()
        by_name = {p["name"]: p for p in body["providers"]}
        assert body["api_version"] == plugins.API_VERSION
        assert by_name["youtube"] == {"name": "youtube", "builtin": True, "api_version": 1,
                                      "adult": False, "enabled": True, "adult_export": False,
                                      "error": None}
        assert by_name["example"]["enabled"] is False
        assert by_name["adultex"]["adult"] is True
        assert "API_VERSION is 0" in by_name["oldapi"]["error"]
        assert "providers" in c.get("/api/v1/meta").json()["features"]

        r = c.patch("/api/v1/settings", json={"enabled_providers": ["youtube", "example"]})
        assert r.status_code == 200
        assert {p["name"]: p["enabled"] for p in c.get("/api/v1/providers").json()["providers"]
                }["example"] is True
        assert c.patch("/api/v1/settings", json={"enabled_providers": ["Bad Name"]}).status_code == 400

        made = c.post("/subscriptions", json={"url": "https://example.test/f",
                                                     "backend": "example"})
        assert made.status_code in (200, 201), made.text
        refused = c.post("/subscriptions", json={"url": "https://adult.example.test/f",
                                                        "backend": "adultex"})
        assert refused.status_code == 400 and "not enabled" in refused.text
        assert c.post("/subscriptions", json={"url": "https://x.test/f",
                                                     "backend": "nothing"}).status_code == 422
        assert c.get("/api/v1/healthz").json() == {"status": "ok"}


def seed_subscription(db, backend):
    index = indexdb.open_index(db)
    sub_id = f"{backend}:url:https://adult.example.test/feed"
    with index.write() as conn:
        conn.execute("INSERT INTO subscriptions (id, url, backend, kind, created_at)"
                     " VALUES (?, ?, ?, 'channel', ?)",
                     (sub_id, "https://adult.example.test/feed", backend, "2024-01-01T00:00:00Z"))
        conn.execute("INSERT INTO memberships (subscription_id, entry_id, position, first_seen)"
                     " VALUES (?, ?, 0, ?)",
                     (sub_id, stable_id(Source.YOUTUBE, "https://www.youtube.com/watch?v=vidAAAAAAAA"),
                      "2024-01-01T00:00:00Z"))
    return sub_id


def files(target):
    return sorted(p for p in Path(target).rglob("*") if p.is_file()) if Path(target).exists() else []


def test_an_adult_provider_is_refused_for_export_until_switched_on(env, loaded):
    target = env["target"]
    with client(env) as c:
        sub_id = seed_subscription(env["db"], "adultex")
        r = export(c, scope="subscription", id=sub_id, target_dir=str(target),
                   jellyfin_refresh=True)
        assert r.status_code == 400 and "adultex is an adult provider" in r.text
        assert files(target) == []

        c.patch("/api/v1/settings", json={"adult_export_providers": ["adultex"]})
        r = export(c, scope="subscription", id=sub_id, target_dir=str(target))
        assert r.status_code == 201, r.text
        wait_done(c, r.json()["id"])
        assert any(p.suffix == ".strm" for p in files(target))
        provider = {p["name"]: p for p in c.get("/api/v1/providers").json()["providers"]}["adultex"]
        assert provider["adult_export"] is True


def without(monkeypatch, *names):
    monkeypatch.setattr(plugins, "BUILTIN", {k: v for k, v in plugins.BUILTIN.items()
                                             if k not in names})


def test_the_switch_is_per_provider(env, loaded):
    with client(env) as c:
        c.patch("/api/v1/settings", json={"adult_export_providers": ["example"]})
        sub_id = seed_subscription(env["db"], "adultex")
        r = export(c, scope="subscription", id=sub_id, target_dir=str(env["target"]))
        assert r.status_code == 400 and "adultex" in r.text


def test_entries_of_an_adult_source_are_refused_in_entry_scope(env, monkeypatch):
    without(monkeypatch, "ia")
    plugins.reload([ep("adultia", "AdultInternetArchiveProvider")])
    db = EnvelopeJsonStorage(env["db"])
    db["https://archive.org/details/x"] = {"source": "internet_archive",
                                           "url": "https://archive.org/details/x", "title": "T",
                                           "streams": ["https://archive.org/download/x/x.mp4"]}
    db.store()
    with client(env) as c:
        entry = stable_id(Source.INTERNET_ARCHIVE, "https://archive.org/details/x")
        r = export(c, scope="entry", id=entry, target_dir=str(env["target"]))
        assert r.status_code == 400 and "adultia is an adult provider" in r.text
        assert files(env["target"]) == []
        c.patch("/api/v1/settings", json={"adult_export_providers": ["adultia"]})
        assert export(c, scope="entry", id=entry, target_dir=str(env["target"])).status_code == 201


def test_the_automatic_export_obeys_the_guard(env, loaded, caplog, monkeypatch):
    monkeypatch.setenv(links.ENV_PUBLIC_URL, "http://archivist.local:8000")
    sub_id = seed_subscription(env["db"], "adultex")
    index = indexdb.open_index(env["db"])
    with index.write() as conn:
        conn.execute("UPDATE subscriptions SET export_automatic = 1, export_target = ?,"
                     " export_layout = 'movies', export_links = 'plain' WHERE id = ?",
                     (str(env["target"]), sub_id))
    entry = stable_id(Source.YOUTUBE, "https://www.youtube.com/watch?v=vidAAAAAAAA")
    caplog.set_level(logging.WARNING)
    assert export_hooks.on_entries_added(sub_id, [entry], db_path=env["db"]) is None
    assert "adultex is an adult provider" in caplog.text
    assert files(env["target"]) == []


def test_the_command_line_export_obeys_the_guard(env, monkeypatch):
    without(monkeypatch, "youtube")
    plugins.reload([ep("adultyt", "AdultYoutubeProvider")])
    out = env["tmp"] / "out"
    with pytest.raises(strm.ExportRefused, match="adultyt is an adult provider"):
        strm.export_strm(env["db"], str(out))
    assert not out.exists()
    Settings(indexdb.open_index(env["db"])).update({"adult_export_providers": ["adultyt"]})
    assert strm.export_strm(env["db"], str(out)) > 0


def entry_id():
    return stable_id(Source.YOUTUBE, "https://www.youtube.com/watch?v=vidAAAAAAAA")


def test_an_entry_of_an_adult_subscription_is_refused_whatever_its_source(env, loaded):
    with client(env) as c:
        seed_subscription(env["db"], "adultex")
        r = export(c, scope="entry", id=entry_id(), target_dir=str(env["target"]))
        assert r.status_code == 400 and "adultex is an adult provider" in r.text
        assert files(env["target"]) == []


def test_a_subscription_id_in_playlist_scope_is_refused(env, loaded):
    with client(env) as c:
        sub_id = seed_subscription(env["db"], "adultex")
        r = export(c, scope="playlist", id=sub_id, target_dir=str(env["target"]),
                   jellyfin_refresh=True, collection=True)
        assert r.status_code == 400 and "adultex is an adult provider" in r.text
        assert files(env["target"]) == []


def test_an_empty_adult_subscription_is_refused_in_playlist_scope(env, loaded):
    with client(env) as c:
        sub_id = seed_subscription(env["db"], "adultex")
        with indexdb.open_index(env["db"]).write() as conn:
            conn.execute("DELETE FROM memberships")
        r = export(c, scope="playlist", id=sub_id, target_dir=str(env["target"]))
        assert r.status_code == 400 and "adultex is an adult provider" in r.text


def test_the_command_line_export_refuses_entries_of_an_adult_subscription(env, loaded):
    seed_subscription(env["db"], "adultex")
    out = env["tmp"] / "out"
    with pytest.raises(strm.ExportRefused, match="adultex is an adult provider"):
        strm.export_strm(env["db"], str(out))
    assert not out.exists()


def test_a_provider_that_is_not_loaded_fails_closed(env, loaded):
    with client(env) as c:
        sub_id = seed_subscription(env["db"], "adultex")
        for registry in ([], [ep("adultex", "OldApiProvider")]):
            plugins.reload(registry)
            for scope, ident in (("subscription", sub_id), ("playlist", sub_id),
                                 ("entry", entry_id())):
                r = export(c, scope=scope, id=ident, target_dir=str(env["target"]))
                assert r.status_code == 400 and "adultex is not loaded" in r.text, (scope, r.text)
        assert files(env["target"]) == []


def test_a_built_in_whose_client_is_missing_does_not_block_exports(env, monkeypatch):
    monkeypatch.setitem(plugins.BUILTIN, "bandcamp", "no_such_module_anywhere:X")
    plugins.reload()
    assert "bandcamp" in plugins.registry().errors
    with client(env) as c:
        seed_subscription(env["db"], "bandcamp")
        r = export(c, scope="subscription", id="bandcamp:url:https://adult.example.test/feed",
                   target_dir=str(env["target"]))
        assert r.status_code == 201, r.text
        wait_done(c, r.json()["id"])


def test_a_plugin_that_exits_while_importing_is_reported(tmp_path, monkeypatch):
    (tmp_path / "exiting_plugin.py").write_text("raise SystemExit(3)\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    reg = plugins.reload([*GOOD, ep("exiting", "X", module="exiting_plugin")])
    assert "SystemExit" in reg.errors["exiting"]
    assert {"example", "youtube"} <= set(reg.providers)


def test_names_are_lowercase_and_settings_name_installed_providers(env):
    reg = plugins.reload([ep("Example", "ExampleProvider"), *GOOD[1:]])
    assert "lowercase" in reg.errors["Example"]
    with client(env) as c:
        for key in ("enabled_providers", "adult_export_providers"):
            r = c.patch("/api/v1/settings", json={key: ["nothing_here"]})
            assert r.status_code == 400 and "no installed provider named nothing_here" in r.text
            assert c.patch("/api/v1/settings", json={key: ["Example"]}).status_code == 400
        assert c.patch("/api/v1/settings", json={"enabled_providers": []}).status_code == 200
        assert c.patch("/api/v1/settings", json={"enabled_providers": ["adultex"]}).status_code == 200


def test_a_name_clash_is_listed_once_with_the_clashing_value(env):
    plugins.reload([ep("youtube", "ExampleProvider")])
    with client(env) as c:
        rows = c.get("/api/v1/providers").json()["providers"]
    assert [r["name"] for r in rows].count("youtube") == 1
    assert any(r["name"].startswith("youtube [") and "already registered" in r["error"] for r in rows)


def stamped_id(video_id):
    return stable_id(Source.YOUTUBE, f"https://www.youtube.com/watch?v={video_id}")


STAMPING = [ep("stampadult", "StampingAdultProvider"), ep("stampplain", "StampingPlainProvider")]


def archive_via_task(c, env, backend, url, video_id):
    from media_archivist.index import Index

    r = c.post("/archive", json={"kind": "archive", "url": url, "backend": backend})
    assert r.status_code == 200, r.text
    deadline = time.monotonic() + 10
    while c.get(f"/tasks/{r.json()['id']}").json()["status"] in ("queued", "running"):
        assert time.monotonic() < deadline
        time.sleep(0.05)
    assert Index(env["db"]).get(stamped_id(video_id)) is not None


def test_an_entry_archived_by_an_adult_plugin_is_refused(env):
    plugins.reload(STAMPING)
    with client(env) as c:
        enable(env["db"], "stampadult", "stampplain")
        archive_via_task(c, env, "stampadult", "https://adult.example.test/a", "vidZZZZZZZZ")
        r = export(c, scope="entry", id=stamped_id("vidZZZZZZZZ"), target_dir=str(env["target"]))
        assert r.status_code == 400 and "stampadult is an adult provider" in r.text
        assert files(env["target"]) == []


def test_an_entry_archived_by_a_plain_plugin_still_exports(env):
    plugins.reload(STAMPING)
    with client(env) as c:
        enable(env["db"], "stampadult", "stampplain")
        archive_via_task(c, env, "stampplain", "https://example.test/a", "vidYYYYYYYY")
        r = export(c, scope="entry", id=stamped_id("vidYYYYYYYY"), target_dir=str(env["target"]))
        assert r.status_code == 201, r.text
        wait_done(c, r.json()["id"])


def test_an_entry_archived_by_a_synced_adult_subscription_is_refused(env):
    plugins.reload(STAMPING)
    with client(env) as c:
        enable(env["db"], "stampadult", "stampplain")
        sub = subs.add_subscription(env["db"], "https://adult.example.test/feed", backend="stampadult")
        assert subs.sync_subscription(env["db"], sub).ok
        r = export(c, scope="entry", id=stamped_id("vidZZZZZZZZ"), target_dir=str(env["target"]))
        assert r.status_code == 400 and "stampadult is an adult provider" in r.text
        assert files(env["target"]) == []


def test_the_command_line_export_refuses_an_entry_archived_by_an_adult_plugin(env):
    plugins.reload(STAMPING)
    with client(env):
        enable(env["db"], "stampadult", "stampplain")
        sub = subs.add_subscription(env["db"], "https://adult.example.test/feed", backend="stampadult")
        assert subs.sync_subscription(env["db"], sub).ok
    out = env["tmp"] / "out"
    with pytest.raises(strm.ExportRefused, match="stampadult is an adult provider"):
        strm.export_strm(env["db"], str(out))
    assert not out.exists()


def test_a_later_archive_by_another_backend_keeps_the_adult_record(env):
    plugins.reload(STAMPING)
    with client(env) as c:
        enable(env["db"], "stampadult", "stampplain")
        archive_via_task(c, env, "stampadult", "https://adult.example.test/a", "vidZZZZZZZZ")
        plain = subs.add_subscription(env["db"], "https://example.test/again", backend="stampplain")
        row = EnvelopeJsonStorage(env["db"])["https://www.youtube.com/watch?v=vidZZZZZZZZ"]
        row["title"] = "Changed so the rewrite is a change"
        db = EnvelopeJsonStorage(env["db"])
        db["https://www.youtube.com/watch?v=vidZZZZZZZZ"] = row
        db.store()
        assert subs.sync_subscription(env["db"], plain).ok
        stored = EnvelopeJsonStorage(env["db"])["https://www.youtube.com/watch?v=vidZZZZZZZZ"]
        assert stored["archived_by"] == ["stampadult", "stampplain"]
        r = export(c, scope="entry", id=stamped_id("vidZZZZZZZZ"), target_dir=str(env["target"]))
        assert r.status_code == 400


def test_an_uppercase_entry_point_name_is_a_load_error():
    reg = plugins.reload([ep("Upper", "ExampleProvider")])
    assert "Upper" not in reg.providers
    assert "lowercase letters, digits" in reg.errors["Upper"]


ZROW = "https://www.youtube.com/watch?v=vidZZZZZZZZ"


def test_archiving_the_same_url_again_keeps_the_adult_record(env):
    plugins.reload(STAMPING)
    with client(env) as c:
        enable(env["db"], "stampadult", "stampplain")
        for _ in range(2):
            archive_via_task(c, env, "stampadult", "https://adult.example.test/a", "vidZZZZZZZZ")
            assert EnvelopeJsonStorage(env["db"])[ZROW]["archived_by"] == ["stampadult"]
            r = export(c, scope="entry", id=stamped_id("vidZZZZZZZZ"), target_dir=str(env["target"]))
            assert r.status_code == 400, r.text
        assert files(env["target"]) == []


def test_a_plain_backend_rewriting_an_identical_row_keeps_the_adult_record(env):
    plugins.reload(STAMPING)
    with client(env) as c:
        enable(env["db"], "stampadult", "stampplain")
        archive_via_task(c, env, "stampadult", "https://adult.example.test/a", "vidZZZZZZZZ")
        archive_via_task(c, env, "stampplain", "https://example.test/again", "vidZZZZZZZZ")
        assert EnvelopeJsonStorage(env["db"])[ZROW]["archived_by"] == ["stampadult", "stampplain"]
        r = export(c, scope="entry", id=stamped_id("vidZZZZZZZZ"), target_dir=str(env["target"]))
        assert r.status_code == 400


def test_rows_written_before_the_archivist_raises_are_stamped(env):
    plugins.reload([ep("raiser", "StampThenRaiseProvider")])
    with client(env) as c:
        enable(env["db"], "raiser")
        sub = subs.add_subscription(env["db"], "https://adult.example.test/feed", backend="raiser")
        result = subs.sync_subscription(env["db"], sub)
        assert not result.ok and "failed after writing" in result.error
        assert EnvelopeJsonStorage(env["db"])[ZROW]["archived_by"] == ["raiser"]
        r = export(c, scope="entry", id=stamped_id("vidZZZZZZZZ"), target_dir=str(env["target"]))
        assert r.status_code == 400


def test_a_failure_while_stamping_is_logged_and_does_not_hide_the_archive(env, monkeypatch, caplog):
    def broken(*args):
        raise OSError("disk full")

    monkeypatch.setattr(plugins, "_stamp", broken)
    plugins.reload(STAMPING)
    archivist = plugins.registry().providers["stampplain"].cls(db_path=env["db"])
    with caplog.at_level(logging.ERROR):
        plugins.archive_and_stamp(archivist, "stampplain", "https://example.test/a")
    assert "could not record 'stampplain'" in caplog.text
    assert EnvelopeJsonStorage(env["db"])["https://www.youtube.com/watch?v=vidYYYYYYYY"]


def test_the_add_command_stamps_what_it_archives(env, monkeypatch):
    from types import SimpleNamespace

    from media_archivist.cli import main

    plugins.reload(STAMPING)
    cls = plugins.registry().providers["stampadult"].cls
    monkeypatch.setattr(plugins, "get", lambda *a, **k: SimpleNamespace(cls=cls))
    assert main(["add", "--db-file", env["db"], "https://example.test/a"]) == 0
    assert EnvelopeJsonStorage(env["db"])[ZROW]["archived_by"] == ["youtube"]


def archive_directly(env, attr, name="someplugin", prior=None):
    plugins.reload([ep(name, attr)])
    if prior is not None:
        db = EnvelopeJsonStorage(env["db"])
        db[ZROW] = {"source": "youtube", "url": ZROW, "videoId": "vidZZZZZZZZ",
                    "title": "Stamped", "author": "x", "stream": "http://x/y.mp4",
                    "archived_by": prior}
        db.store()
    archivist = plugins.registry().providers[name].cls(db_path=env["db"])
    plugins.archive_and_stamp(archivist, name, "https://example.test/a")
    return EnvelopeJsonStorage(env["db"]).get(ZROW)


def test_update_with_a_generator_stores_and_records_the_rows(env):
    row = archive_directly(env, "UpdateGeneratorProvider")
    assert row is not None and row["archived_by"] == ["someplugin"]


def test_setdefault_records_a_row_it_leaves_unchanged(env):
    row = archive_directly(env, "SetdefaultProvider", prior=["stampadult"])
    assert row["archived_by"] == ["someplugin", "stampadult"]


def test_a_database_without_a_setter_is_stamped_from_changed_rows(env):
    row = archive_directly(env, "ReadOnlyDbProvider")
    assert row["archived_by"] == ["someplugin"]


def test_a_plugin_that_replaces_its_database_is_stamped(env):
    row = archive_directly(env, "ReplacingDbProvider")
    assert row["archived_by"] == ["someplugin"]


def test_a_kept_handle_to_the_database_is_logged(env, caplog):
    with caplog.at_level(logging.WARNING):
        row = archive_directly(env, "KeptHandleProvider")
    assert "keeps its own handle to the database (_handle)" in caplog.text
    assert row["archived_by"] == ["someplugin"]


def test_update_with_an_identical_row_is_recorded(env):
    row = archive_directly(env, "UpdateDictProvider", prior=["stampadult"])
    assert row["archived_by"] == ["someplugin", "stampadult"]


def test_the_original_database_object_is_restored(env):
    plugins.reload([ep("someplugin", "ExampleProvider")])
    archivist = plugins.registry().providers["someplugin"].cls(db_path=env["db"])
    original = archivist.db
    plugins.archive_and_stamp(archivist, "someplugin", "https://example.test/a")
    assert archivist.db is original


def test_a_db_setter_that_raises_anything_still_lets_the_archive_run(env):
    row = archive_directly(env, "RaisingSetterProvider")
    assert row["archived_by"] == ["someplugin"]


def test_a_refused_restore_is_logged_and_the_rows_are_still_stamped(env, caplog):
    with caplog.at_level(logging.WARNING):
        row = archive_directly(env, "RefusesRestoreProvider")
    assert "RefusesRestoreProvider refused to take its database back (RuntimeError)" in caplog.text
    assert row["archived_by"] == ["someplugin"]
