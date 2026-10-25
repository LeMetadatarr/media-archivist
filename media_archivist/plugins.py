# SPDX-License-Identifier: Apache-2.0
"""Providers loaded through Python entry points.

A provider is a class registered under the ``media_archivist.providers``
entry-point group. The entry-point name is the provider's name. The five
built-in providers register through the same mechanism as an installed
plugin; their entry points are also declared in ``pyproject.toml``.

The class is checked when it loads:

* ``API_VERSION`` is an integer equal to :data:`API_VERSION`.
* ``archive(url)`` is callable, and the class accepts the keyword arguments
  ``db_path``, ``required_kwords``, ``blacklisted_kwords`` and
  ``min_duration`` (see :class:`ProviderProtocol`).
* ``adult``, when present, is a bool; ``url_hosts`` and ``sources`` are
  tuples of strings, and no other provider claims one of the ``sources``.

Installed is not enabled. A plugin works only when its name is in the
``enabled_providers`` setting. A plugin that fails to import, exits while
importing or fails the check is recorded with its error and skipped. Loading
runs the plugin's module code in the server process, so a module that never
returns from its import blocks the first request that needs the providers;
that is not guarded against. A provider with ``adult = True`` is refused for
``.strm`` export and Jellyfin refresh until its name is in the
``adult_export_providers`` setting.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points
from typing import Any, Dict, Iterable, List, Optional, Protocol, Tuple

LOG = logging.getLogger("media_archivist.plugins")

API_VERSION = 1
GROUP = "media_archivist.providers"

BUILTIN: Dict[str, str] = {
    "youtube": "media_archivist.youtube:YoutubeArchivist",
    "ia": "media_archivist.ia:IAArchivist",
    "music": "media_archivist.music:YoutubeMusicArchivist",
    "bandcamp": "media_archivist.bandcamp:BandcampArchivist",
    "soundcloud": "media_archivist.soundcloud:SoundCloudArchivist",
}
BUILTIN_NAMES: Tuple[str, ...] = tuple(BUILTIN)
NAME = re.compile(r"[a-z0-9][a-z0-9_-]*")


class ProviderProtocol(Protocol):
    """What a provider class offers; ``JsonArchivist`` already does."""

    API_VERSION: int
    adult: bool
    url_hosts: Tuple[str, ...]
    sources: Tuple[str, ...]

    def __init__(self, *, db_path: Optional[str] = None, required_kwords=None,
                 blacklisted_kwords=None, min_duration: int = -1) -> None: ...

    @property
    def video_urls(self) -> List[str]: ...

    def archive(self, url: str) -> None: ...


class ProviderUnavailable(ValueError):
    """The named provider is unknown, failed to load, or is not enabled."""


class AdultProviderRefused(ValueError):
    """An export involves an adult provider that is not switched on."""


@dataclass(frozen=True)
class Provider:
    name: str
    cls: Any
    builtin: bool
    adult: bool
    url_hosts: Tuple[str, ...]
    sources: Tuple[str, ...]
    api_version: int


@dataclass
class Registry:
    providers: Dict[str, Provider]
    errors: Dict[str, str]


def _string_tuple(cls, attr: str) -> Tuple[str, ...]:
    value = getattr(cls, attr, ())
    if not isinstance(value, tuple) or not all(isinstance(v, str) and v for v in value):
        raise ValueError(f"{attr} must be a tuple of non-empty strings")
    return value


def _provider_from(ep: EntryPoint, builtin: bool) -> Provider:
    if not NAME.fullmatch(ep.name):
        raise ValueError("the entry-point name must be lowercase letters, digits, - and _")
    cls = ep.load()
    version = getattr(cls, "API_VERSION", None)
    if isinstance(version, bool) or version != API_VERSION:
        raise ValueError(f"API_VERSION is {version!r}, this server speaks {API_VERSION}")
    if not callable(getattr(cls, "archive", None)):
        raise ValueError("the class has no archive(url) method")
    adult = getattr(cls, "adult", False)
    if not isinstance(adult, bool):
        raise ValueError("adult must be a bool")
    return Provider(name=ep.name, cls=cls, builtin=builtin, adult=adult,
                    url_hosts=tuple(h.lower() for h in _string_tuple(cls, "url_hosts")),
                    sources=_string_tuple(cls, "sources"), api_version=version)


def _installed() -> List[EntryPoint]:
    found = entry_points()
    return list(found.select(group=GROUP) if hasattr(found, "select") else found.get(GROUP, []))


def build_registry(installed: Optional[Iterable[EntryPoint]] = None) -> Registry:
    """Load the built-in providers, then every installed entry point.

    A name registered twice keeps its first registration, so a plugin
    cannot replace a built-in provider.
    """
    providers: Dict[str, Provider] = {}
    errors: Dict[str, str] = {}
    values: Dict[str, str] = {}
    candidates = [(EntryPoint(name, value, GROUP), True) for name, value in BUILTIN.items()]
    try:
        candidates += [(ep, False) for ep in (_installed() if installed is None else installed)]
    except Exception as e:
        LOG.warning("entry points of %s could not be read: %s", GROUP, e)
        errors["<entry points>"] = f"{type(e).__name__}: {e}"
    for ep, builtin in candidates:
        if ep.name in values:
            if values[ep.name] != ep.value:
                errors[f"{ep.name} [{ep.value}]"] = f"name already registered by {values[ep.name]}"
            continue
        values[ep.name] = ep.value
        try:
            provider = _provider_from(ep, builtin)
            for other in providers.values():
                clash = set(other.sources) & set(provider.sources)
                if clash:
                    raise ValueError(f"sources {sorted(clash)} are already claimed by {other.name!r}")
            providers[ep.name] = provider
        except BaseException as e:  # a plugin may call sys.exit() while importing
            if isinstance(e, KeyboardInterrupt):
                raise
            LOG.warning("provider %s (%s) not loaded: %s", ep.name, ep.value, e)
            errors[ep.name] = f"{type(e).__name__}: {e}"
    return Registry(providers, errors)


_lock = threading.Lock()
_registry: Optional[Registry] = None


def registry() -> Registry:
    global _registry
    with _lock:
        if _registry is None:
            _registry = build_registry()
        return _registry


def reload(installed: Optional[Iterable[EntryPoint]] = None) -> Registry:
    """Discover the entry points again; ``installed`` replaces discovery."""
    global _registry
    fresh = build_registry(installed)
    with _lock:
        _registry = fresh
    return fresh


def enabled_names(settings) -> List[str]:
    return list(settings.get("enabled_providers"))


def get(name: str, settings=None) -> Provider:
    """The provider ``name``; with ``settings``, only when it is enabled."""
    reg = registry()
    provider = reg.providers.get(name)
    if provider is None:
        if name in reg.errors:
            raise ProviderUnavailable(f"provider {name!r} failed to load: {reg.errors[name]}")
        raise ProviderUnavailable(f"unknown backend: {name!r}")
    if settings is not None and name not in enabled_names(settings):
        raise ProviderUnavailable(f"provider {name!r} is installed but not enabled")
    return provider


def enabled(settings) -> List[Provider]:
    reg = registry()
    return [reg.providers[n] for n in enabled_names(settings) if n in reg.providers]


def infer(url: str, settings=None) -> Optional[str]:
    """The provider whose host appears in ``url``, longest host first.

    With ``settings``, only enabled providers are considered; the built-in
    providers are always considered so a URL keeps its backend.
    """
    low = (url or "").lower()
    candidates = [p for p in registry().providers.values()
                  if p.builtin or settings is None or p.name in enabled_names(settings)]
    hosts = sorted(((h, p.name) for p in candidates for h in p.url_hosts),
                   key=lambda pair: -len(pair[0]))
    for host, name in hosts:
        if host in low:
            return name
    return None


def provider_of_source(source: str) -> Optional[Provider]:
    for provider in registry().providers.values():
        if source in provider.sources:
            return provider
    return None


def known_names() -> List[str]:
    """Names of every provider that is installed, loaded or not."""
    reg = registry()
    return sorted({*reg.providers, *(k.split(" [")[0] for k in reg.errors)})


def check_adult_export(settings, names: Iterable[str]) -> None:
    """Raise :class:`AdultProviderRefused` unless every provider may export.

    Refused are an adult provider not switched on and a provider that is not
    loaded (uninstalled, or failed to load), whose flag is unknown. A built-in
    provider whose client library is missing is not adult and passes.
    """
    allowed = set(settings.get("adult_export_providers"))
    reg = registry().providers
    unloaded = sorted(n for n in set(names) if n not in reg and n not in BUILTIN)
    if unloaded:
        raise AdultProviderRefused(
            f"export refused: provider {', '.join(unloaded)} is not loaded, "
            "so it cannot be shown not to be an adult provider")
    refused = sorted(n for n in set(names) if n in reg and reg[n].adult and n not in allowed)
    if refused:
        raise AdultProviderRefused(
            f"export refused: {', '.join(refused)} is an adult provider; "
            "add it to the adult_export_providers setting to allow .strm export "
            "and Jellyfin refresh")


ARCHIVED_BY = "archived_by"


def _signature(row: Any) -> str:
    body = {k: v for k, v in row.items() if k != ARCHIVED_BY} if isinstance(row, dict) else row
    return json.dumps(body, sort_keys=True, default=str)


class _Writes:
    """The archivist's database, remembering which keys were assigned."""

    def __init__(self, db) -> None:
        self._db = db
        self.touched: set = set()

    def __getattr__(self, name: str):
        return getattr(self._db, name)

    def __getitem__(self, key):
        return self._db[key]

    def __setitem__(self, key, value) -> None:
        self.touched.add(key)
        self._db[key] = value

    def __delitem__(self, key) -> None:
        del self._db[key]

    def __contains__(self, key) -> bool:
        return key in self._db

    def __iter__(self):
        return iter(self._db)

    def __len__(self) -> int:
        return len(self._db)

    def setdefault(self, key, default=None):
        self.touched.add(key)
        return self._db.setdefault(key, default)

    def update(self, *args, **kwargs) -> None:
        items = dict(*args, **kwargs)
        self.touched.update(items)
        self._db.update(items)


def archive_and_stamp(archivist, name: str, url: str) -> None:
    """Run ``archivist.archive(url)`` and record ``name`` on the rows it wrote.

    A row the archive call assigned, or that is new or differs afterwards,
    gets ``archived_by``: the backends it carried before, the ones the
    archivist left on it, and ``name``. The set only grows, so archiving a
    row again, or through another backend, never removes an adult provider,
    whether or not the content changed. Rows are stamped even when the
    archivist raises after writing them. An archivist whose ``db`` cannot be
    replaced, or that replaces it itself, is stamped from the rows that are
    new or changed.
    """
    original = archivist.db
    before = {k: (_signature(v), tuple(v.get(ARCHIVED_BY) or ())) for k, v in original.items()
              if isinstance(v, dict)}
    writes = _Writes(original)
    try:
        archivist.db = writes
    except Exception:
        pass
    try:
        archivist.archive(url)
    finally:
        current = archivist.db
        if current is writes:
            try:
                archivist.db = original
            except Exception as e:
                LOG.warning("%s refused to take its database back (%s); it keeps the "
                            "recording wrapper", type(archivist).__name__, type(e).__name__)
            current = original
        targets = [(original, writes.touched)]
        if current is not original and current is not writes:
            targets = [(current, set())] + ([targets[0]] if writes.touched else [])
        for db, touched in targets:
            try:
                _stamp(db, before, touched, name)
            except Exception:
                LOG.exception("could not record %r on the rows it archived from %s", name, url)
        kept = [k for k, v in getattr(archivist, "__dict__", {}).items()
                if v is original and k != "db"]
        if kept:
            LOG.warning("%s keeps its own handle to the database (%s); a row it rewrites "
                        "unchanged through it is not recorded as archived by %r",
                        type(archivist).__name__, ", ".join(kept), name)


def _stamp(db, before: dict, touched: set, name: str) -> None:
    changed = False
    for key, row in list(db.items()):
        if not isinstance(row, dict):
            continue
        prior = before.get(key)
        if key not in touched and prior is not None and prior[0] == _signature(row):
            continue
        wanted = sorted({*(prior[1] if prior else ()), *(row.get(ARCHIVED_BY) or ()), name})
        if row.get(ARCHIVED_BY) != wanted:
            db[key] = {**row, ARCHIVED_BY: wanted}
            changed = True
    if changed:
        db.store()
