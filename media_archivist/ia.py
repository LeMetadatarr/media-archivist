from __future__ import annotations

from typing import Any, Dict, List

import internetarchive as ia
import requests

from media_archivist.base import JsonArchivist


class IAArchivist(JsonArchivist):
    """Index Internet Archive items and collections into a JSON-backed DB."""

    VALID_FORMATS = ["MPEG2", "Ogg Video", "512Kb MPEG4", "h.264"]

    def archive(self, identifier: str) -> None:
        """Detect item-vs-collection via the IA metadata API, not exception flow."""
        try:
            meta = requests.get(
                f"https://archive.org/metadata/{identifier}", timeout=30
            ).json()
        except Exception:
            self.log.exception("Failed to query IA metadata for %s", identifier)
            return

        m = meta.get("metadata") or {}
        if m.get("mediatype") == "collection":
            self.archive_collection(identifier)
        elif m:
            self.archive_item(identifier)
        else:
            self.archive_collection(identifier)

    def bootstrap_from_url(self, url: str) -> None:
        self.log.info("Bootstrapping database from: %s", url)
        self.db.update(requests.get(url, timeout=30).json())
        self.db.store()

    def archive_item(self, item_id: str) -> None:
        item = ia.get_item(item_id)
        meta: Dict[str, Any] = requests.get(item.urls.metadata, timeout=30).json()
        m = meta["metadata"]

        tags: List[str] = []
        subject = m.get("subject")
        if isinstance(subject, str):
            tags += subject.split(";")
        elif isinstance(subject, list):
            tags += subject

        title = m["title"]
        if isinstance(title, list):
            title = title[0]

        from media_archivist.models import RawIAEntry

        streams: List[str] = []
        images: List[str] = []
        for f in meta["files"]:
            if f["format"] in self.VALID_FORMATS:
                streams.append(item.urls.download + "/" + f["name"])
            elif f["format"] == "PNG":
                images.append(item.urls.download + "/" + f["name"])

        if not streams:
            return

        title_l = title.lower()
        if any(k.lower() in title_l for k in self.blacklisted_kwords):
            return
        if self.required_kwords and not all(k.lower() in title_l for k in self.required_kwords):
            return

        # IA item URL — used as the canonical entry URL.
        item_url = f"https://archive.org/details/{item_id}"
        entry = RawIAEntry(
            url=item_url,
            title=title,
            tags=tags,
            collection=m["collection"],
            duration=m.get("runtime"),
            streams=streams,
            images=images,
        )
        self.log.info("Parsing video %s", title)
        # Key by IA identifier (legacy) but store the validated payload.
        self.db[item_id] = entry.model_dump(mode="json")
        self.db.store()

    def archive_collection(self, collection_name: str) -> None:
        from media_archivist.progress import progress
        session = ia.ArchiveSession()
        for entry in progress(ia.Search(session, "collection:" + collection_name),
                              desc=f"collection {collection_name}", unit="item"):
            item_id = entry["identifier"]
            if item_id not in self.db:
                self.archive_item(item_id)
