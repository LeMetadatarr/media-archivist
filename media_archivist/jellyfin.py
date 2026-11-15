# SPDX-License-Identifier: Apache-2.0
"""What an export asks of Jellyfin: a library refresh and a collection.

The connection is ``MEDIA_ARCHIVIST_JELLYFIN_URL`` and
``MEDIA_ARCHIVIST_JELLYFIN_API_KEY``; the key travels in the
``X-Emby-Token`` header. Without both, nothing here is called and an export
reports ``not configured``.

An exported item is found in Jellyfin by its YouTube id, never by its path,
because Jellyfin often sees the library under another path than
media-archivist does: the ``Youtube`` provider id Jellyfin read from the
``.nfo``, or else the ``[videoid]`` every exported file name ends with.

A playlist export's collection is a Jellyfin collection (BoxSet) named like
the playlist and holding exactly the playlist's exported items. The
collections media-archivist made are recorded in the index's
``jellyfin_collections`` table; a collection that is not recorded there is
never changed, and a playlist whose name is taken by such a collection gets
none. A collection is never deleted, and keeps its name when the playlist is
renamed.
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Callable, Dict, Iterable, List, Optional, Sequence

import requests

from media_archivist.indexdb import utcnow

LOG = logging.getLogger("media_archivist.jellyfin")

ENV_URL = "MEDIA_ARCHIVIST_JELLYFIN_URL"
ENV_API_KEY = "MEDIA_ARCHIVIST_JELLYFIN_API_KEY"
WAIT_S = 600.0
POLL_S = 15.0
PAGE = 500
ITEM_TYPES = "Movie,Episode"

_TAGGED_NAME = re.compile(r"\[([0-9A-Za-z_-]{6,})\]\.[^./\\]+$")


class JellyfinError(Exception):
    """Jellyfin could not be reached or refused a request."""


def video_id_of(item: Dict) -> Optional[str]:
    """The YouTube id of a Jellyfin item: its provider id, else its file name's ``[id]``."""
    for name, value in (item.get("ProviderIds") or {}).items():
        if name.lower() == "youtube" and value:
            return str(value)
    m = _TAGGED_NAME.search(item.get("Path") or "")
    return m[1] if m else None


class Jellyfin:
    def __init__(self, url: str, api_key: str, *, timeout: float = 30.0) -> None:
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> Optional["Jellyfin"]:
        url = os.environ.get(ENV_URL, "").strip()
        key = os.environ.get(ENV_API_KEY, "").strip()
        return cls(url, key) if url and key else None

    def _call(self, method: str, path: str, **params):
        try:
            r = requests.request(method, f"{self.url}/{path}", params=params or None,
                                 headers={"X-Emby-Token": self.api_key}, timeout=self.timeout)
            r.raise_for_status()
            return r.json() if r.content else None
        except (requests.RequestException, ValueError) as e:
            raise JellyfinError(f"{method} /{path}: {e}") from e

    def _items(self, **query) -> List[Dict]:
        out: List[Dict] = []
        start = 0
        while True:
            page = self._call("GET", "Items", Recursive="true", Fields="Path,ProviderIds",
                              StartIndex=start, Limit=PAGE, **query)
            out += page["Items"]
            start += PAGE
            if start >= page["TotalRecordCount"]:
                return out

    def refresh_library(self) -> None:
        self._call("POST", "Library/Refresh")

    def find_videos(self, video_ids: Iterable[str]) -> Dict[str, str]:
        """YouTube id → Jellyfin item id, for the ids Jellyfin has scanned."""
        wanted = set(video_ids)
        found: Dict[str, str] = {}
        for item in self._items(IncludeItemTypes=ITEM_TYPES):
            vid = video_id_of(item)
            if vid in wanted:
                found.setdefault(vid, item["Id"])
        return found

    def collections(self) -> Dict[str, str]:
        """Jellyfin id → name of every collection."""
        return {b["Id"]: b["Name"] for b in self._items(IncludeItemTypes="BoxSet")}

    def collection_items(self, collection_id: str) -> List[str]:
        return [i["Id"] for i in self._items(ParentId=collection_id)]

    def create_collection(self, name: str, item_ids: Sequence[str]) -> str:
        return self._call("POST", "Collections", Name=name, Ids=",".join(item_ids))["Id"]

    def add_to_collection(self, collection_id: str, item_ids: Sequence[str]) -> None:
        self._call("POST", f"Collections/{collection_id}/Items", Ids=",".join(item_ids))

    def remove_from_collection(self, collection_id: str, item_ids: Sequence[str]) -> None:
        self._call("DELETE", f"Collections/{collection_id}/Items", Ids=",".join(item_ids))


def wait_for_videos(client: Jellyfin, video_ids: Sequence[str], *,
                    sleep: Callable[[float], None] = time.sleep,
                    clock: Callable[[], float] = time.monotonic) -> Dict[str, str]:
    """Poll until Jellyfin has every video or ``WAIT_S`` passes; returns what it has."""
    deadline = clock() + WAIT_S
    while True:
        found = client.find_videos(video_ids)
        if len(found) >= len(set(video_ids)) or clock() >= deadline:
            return found
        sleep(POLL_S)


def sync_collection(index, client: Jellyfin, playlist_id: str, name: str,
                    video_ids: Sequence[str]) -> str:
    """Make or update the collection of ``playlist_id``; returns what happened.

    ``created`` or ``updated``, or a sentence saying why no collection was
    made. Items Jellyfin has not scanned are left out.
    """
    found = wait_for_videos(client, video_ids)
    item_ids = list(dict.fromkeys(found[v] for v in video_ids if v in found))
    row = index.execute("SELECT jellyfin_id FROM jellyfin_collections WHERE playlist_id = ?",
                        (playlist_id,)).fetchone()
    existing = client.collections()
    mine = row["jellyfin_id"] if row and row["jellyfin_id"] in existing else None
    if mine is None and not item_ids:
        return "not made: Jellyfin has not scanned the exported items"
    if mine is None and name in existing.values():
        return "skipped: a collection with this name exists that media-archivist did not make"
    now = utcnow()
    if mine is None:
        mine = client.create_collection(name, item_ids)
        with index.write() as conn:
            conn.execute(
                "INSERT INTO jellyfin_collections (playlist_id, jellyfin_id, name, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?) ON CONFLICT(playlist_id) DO UPDATE SET"
                " jellyfin_id = excluded.jellyfin_id, name = excluded.name,"
                " updated_at = excluded.updated_at", (playlist_id, mine, name, now, now))
        return "created"
    have = client.collection_items(mine)
    add = [i for i in item_ids if i not in have]
    remove = [i for i in have if i not in item_ids]
    if add:
        client.add_to_collection(mine, add)
    if remove:
        client.remove_from_collection(mine, remove)
    with index.write() as conn:
        conn.execute("UPDATE jellyfin_collections SET updated_at = ? WHERE playlist_id = ?",
                     (now, playlist_id))
    return "updated"
