from __future__ import annotations

from logging import Logger, getLogger
from typing import Iterable, List, Optional

from media_archivist.storage import EnvelopeJsonStorage, EnvelopeJsonStorageXDG

LOG = getLogger("media_archivist")


class JsonArchivist:
    def __init__(
        self,
        db_name: Optional[str] = None,
        required_kwords: Optional[Iterable[str]] = None,
        blacklisted_kwords: Optional[Iterable[str]] = None,
        min_duration: int = -1,
        logger: Logger = LOG,
        db_path: Optional[str] = None,
    ) -> None:
        """Open a JSON-backed archive.

        Provide ``db_path`` for an explicit file location (recommended for
        dataset workflows so the file lives next to your scripts), or
        ``db_name`` to auto-place the file under the XDG data dir at
        ``~/.local/share/media_archivist/<name>.json``.
        """
        if db_path and db_name:
            raise ValueError("pass either db_path or db_name, not both")
        self.required_kwords: List[str] = list(required_kwords or [])
        self.blacklisted_kwords: List[str] = list(blacklisted_kwords or [])
        if db_path:
            self.db = EnvelopeJsonStorage(db_path)
        else:
            self.db = EnvelopeJsonStorageXDG(db_name, subfolder="media_archivist")
        self.min_duration = min_duration
        self.log = logger

    @property
    def video_urls(self) -> List[str]:
        return list(self.db.keys())

    def archive(self, url: str) -> None:
        raise NotImplementedError

    def sorted_entries(self) -> list:
        return sorted(
            self.db.values(),
            key=lambda k: k.get("upload_ts") or 0,
            reverse=True,
        )

    def remove_unavailable(self) -> None:
        """Subclass hook — remove entries that are no longer reachable."""

    def remove_keyword(self, kwords: Optional[Iterable[str]] = None) -> None:
        kwords = list(kwords or self.blacklisted_kwords)
        bad_urls = [
            url for url, entry in self.db.items()
            if any(k.lower() in (entry.get("title") or "").lower() for k in kwords)
        ]
        for url in bad_urls:
            self.db.pop(url)
            self.log.info("Removed entry: %s", url)
        self.db.store()

    def remove_missing(self, kwords: Iterable[str]) -> None:
        kwords = list(kwords)
        bad_urls = [
            url for url, entry in self.db.items()
            if any(not entry.get(k) for k in kwords)
        ]
        for url in bad_urls:
            self.db.pop(url)
            self.log.info("Removed entry: %s", url)
        self.db.store()

    def remove_below_duration(self, minutes: int = 30) -> None:
        threshold = minutes * 60
        bad_urls = [
            url for url, entry in self.db.items()
            if (entry.get("duration") or 0) <= threshold
        ]
        for url in bad_urls:
            self.db.pop(url)
            self.log.info("Removed entry: %s", url)
        self.db.store()
