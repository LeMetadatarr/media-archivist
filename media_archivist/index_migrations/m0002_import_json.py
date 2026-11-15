# SPDX-License-Identifier: Apache-2.0
"""Copy what the JSON database already knows into the working index, once.

Every row becomes an ``entries_meta`` row carrying the fields the JSON keeps
for it: duration, upload date, channel, ``metadata_checked`` and
``metadata_network_failed``. Every subscription in the
``<db>.subscriptions.json`` sidecar becomes a ``subscriptions`` row held by
``user``, with its URL, interval and last run. The JSON files are only read.

``first_seen`` is the time of the import for every row, because the JSON
database does not record when a row arrived. A value of the wrong type is
left out rather than copied. When the JSON database or the sidecar cannot
be read, nothing is imported and the step is left to run on the next start.
"""
import json
import logging

from media_archivist.indexdb import subscription_id_for_url, subscription_kind, utcnow

LOG = logging.getLogger("media_archivist.indexdb")

VERSION = 2
NAME = "import json database"


def _entry_values(url, raw, now):
    from media_archivist import ytmeta
    from media_archivist.models.canonical import stable_id
    from media_archivist.models.raw import Source

    try:
        source = Source(raw.get("source"))
    except (TypeError, ValueError):
        return None
    video_id = raw.get("videoId") if source is Source.YOUTUBE else None
    if not isinstance(video_id, str):
        video_id = None
    key = f"youtube:{video_id}" if video_id else f"{source.value}:{url}"
    return (
        stable_id(source, url), key, url, now,
        ytmeta.clean_duration(raw.get("duration")),
        ytmeta.clean_published(raw.get("published")) or None,
        ytmeta.clean_channel(raw.get("author")),
        _text(raw.get("metadata_checked")),
        _text(raw.get(ytmeta.NETWORK_FAILED_KEY)),
    )


def _text(value):
    return value if isinstance(value, str) and value else None


def _read_entries(json_path):
    """The JSON database's rows, ``{}`` when there is none, ``None`` when unreadable."""
    if not json_path.is_file():
        return {}
    try:
        text = json_path.read_text(encoding="utf-8")
        data = json.loads(text) if text.strip() else {}
    except (OSError, ValueError) as e:
        LOG.warning("%s cannot be read (%s); the index import waits for the next start", json_path, e)
        return None
    if data == {}:
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
        LOG.warning("%s is not a media-archivist database; the index import waits for the next start",
                    json_path)
        return None
    return data["entries"]


def _import_entries(conn, entries, now) -> int:
    rows = [v for v in (_entry_values(url, raw, now)
                        for url, raw in entries.items() if isinstance(raw, dict)) if v]
    conn.executemany(
        "INSERT OR IGNORE INTO entries_meta (entry_id, key, url, first_seen, duration,"
        " published, channel, metadata_checked, network_failed_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def _read_subscriptions(json_path):
    from media_archivist import subscriptions as subs_mod

    try:
        return subs_mod.load_subscriptions(str(json_path)).subscriptions
    except Exception as e:
        LOG.warning("the subscriptions of %s cannot be read (%s); the index import waits "
                    "for the next start", json_path, e)
        return None


def _import_subscriptions(conn, subscriptions, now) -> int:
    count = 0
    for sub in subscriptions:
        sid = subscription_id_for_url(sub.url, sub.backend)
        cur = conn.execute(
            "INSERT OR IGNORE INTO subscriptions (id, url, backend, kind, label,"
            " interval_hours, last_listing_at, first_indexed_at, last_error, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (sid, sub.url, sub.backend, subscription_kind(sid), sub.label,
             sub.interval_hours, sub.last_synced_at, sub.last_synced_at,
             sub.last_error, sub.added_at or now))
        if cur.rowcount == 0:
            LOG.warning("subscription %s resolves to %s, already imported from another URL; "
                        "merged into it", sub.url, sid)
        conn.execute("INSERT OR IGNORE INTO subscription_holders (subscription_id, app, added_at)"
                     " VALUES (?, 'user', ?)", (sid, now))
        count += 1
    return count


def apply(conn, index):
    entries = _read_entries(index.json_path)
    subscriptions = _read_subscriptions(index.json_path)
    if entries is None or subscriptions is None:
        return False
    now = utcnow()
    _import_entries(conn, entries, now)
    _import_subscriptions(conn, subscriptions, now)
    return True
