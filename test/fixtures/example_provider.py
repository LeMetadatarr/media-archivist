# SPDX-License-Identifier: Apache-2.0
"""A provider plugin as a third party would ship it, for the loader tests.

It registers under the ``media_archivist.providers`` entry-point group and
indexes nothing: ``archive(url)`` records the URL it was given.
"""
from __future__ import annotations

from media_archivist.base import JsonArchivist


class ExampleProvider(JsonArchivist):
    url_hosts = ("example.test",)
    sources = ("example",)

    def archive(self, url: str) -> None:
        self.db[url] = {"source": "example", "url": url, "title": "example"}
        self.db.store()


class AdultExampleProvider(ExampleProvider):
    adult = True
    url_hosts = ("adult.example.test",)
    sources = ("adult_example",)


class OldApiProvider(ExampleProvider):
    API_VERSION = 0
    sources = ("old_example",)


class NoArchiveProvider:
    API_VERSION = 1


class ExplodingProvider(ExampleProvider):
    def __init__(self, **kwargs):
        raise RuntimeError("the plugin cannot start")


class AdultInternetArchiveProvider(AdultExampleProvider):
    """Claims the source of Internet Archive entries (the test drops that built-in)."""

    sources = ("internet_archive",)


class AdultYoutubeProvider(AdultExampleProvider):
    """Claims the source of YouTube entries (the test drops that built-in)."""

    sources = ("youtube",)


def _store_youtube_row(archivist, video_id: str) -> None:
    url = f"https://www.youtube.com/watch?v={video_id}"
    archivist.db[url] = {"source": "youtube", "url": url, "videoId": video_id,
                         "title": "Stamped", "author": "x", "stream": "http://x/y.mp4"}
    archivist.db.store()


class StampingAdultProvider(AdultExampleProvider):
    """Stores an entry stamped with the built-in YouTube source."""

    sources = ("stamping_adult",)

    def archive(self, url: str) -> None:
        _store_youtube_row(self, "vidZZZZZZZZ")


class StampingPlainProvider(ExampleProvider):
    """A provider that is not adult and stores the same entries under the YouTube source."""

    sources = ("stamping_plain",)

    def archive(self, url: str) -> None:
        _store_youtube_row(self, "vidZZZZZZZZ" if "again" in url else "vidYYYYYYYY")


class StampThenRaiseProvider(AdultExampleProvider):
    """Stores an entry and then fails."""

    sources = ("stamp_then_raise",)

    def archive(self, url: str) -> None:
        _store_youtube_row(self, "vidZZZZZZZZ")
        raise RuntimeError("failed after writing")


_ROW = {"source": "youtube", "url": "https://www.youtube.com/watch?v=vidZZZZZZZZ",
        "videoId": "vidZZZZZZZZ", "title": "Stamped", "author": "x", "stream": "http://x/y.mp4"}
_ROW_URL = _ROW["url"]


class UpdateGeneratorProvider(ExampleProvider):
    """Writes through ``db.update`` with a generator."""

    sources = ("update_generator",)

    def archive(self, url: str) -> None:
        self.db.update((k, v) for k, v in [(_ROW_URL, dict(_ROW))])
        self.db.store()


class SetdefaultProvider(ExampleProvider):
    """Writes through ``db.setdefault``, which leaves an existing row unchanged."""

    sources = ("setdefault",)

    def archive(self, url: str) -> None:
        self.db.setdefault(_ROW_URL, dict(_ROW))
        self.db.store()


class ReadOnlyDbProvider(ExampleProvider):
    """Exposes its database as a property that can be set once, at construction."""

    sources = ("read_only_db",)
    _real_db = None

    @property
    def db(self):
        return self._real_db

    @db.setter
    def db(self, value):
        if self._real_db is not None:
            raise AttributeError("db is read-only")
        self._real_db = value

    def archive(self, url: str) -> None:
        self.db[_ROW_URL] = dict(_ROW)
        self.db.store()


class ReplacingDbProvider(ExampleProvider):
    """Opens a fresh storage object of its own while archiving."""

    sources = ("replacing_db",)

    def archive(self, url: str) -> None:
        from media_archivist.storage import EnvelopeJsonStorage

        self.db = EnvelopeJsonStorage(self.db.path)
        self.db[_ROW_URL] = dict(_ROW)
        self.db.store()


class KeptHandleProvider(ExampleProvider):
    """Keeps a second handle to the database from construction."""

    sources = ("kept_handle",)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._handle = self.db

    def archive(self, url: str) -> None:
        self._handle[_ROW_URL] = dict(_ROW)
        self._handle.store()


class UpdateDictProvider(ExampleProvider):
    """Writes through ``db.update`` with a dict."""

    sources = ("update_dict",)

    def archive(self, url: str) -> None:
        self.db.update({_ROW_URL: dict(_ROW)})
        self.db.store()


class RaisingSetterProvider(ExampleProvider):
    """Its ``db`` setter raises something other than AttributeError once constructed."""

    sources = ("raising_setter",)
    _real_db = None

    @property
    def db(self):
        return self._real_db

    @db.setter
    def db(self, value):
        if self._real_db is not None:
            raise RuntimeError("db may not be replaced")
        self._real_db = value

    def archive(self, url: str) -> None:
        self.db[_ROW_URL] = dict(_ROW)
        self.db.store()


class RefusesRestoreProvider(ExampleProvider):
    """Its ``db`` setter takes the construction and the recorder, then refuses."""

    sources = ("refuses_restore",)
    _real_db = None
    _sets = 0

    @property
    def db(self):
        return self._real_db

    @db.setter
    def db(self, value):
        if self._sets >= 2:
            raise RuntimeError("db may not be put back")
        self._sets += 1
        self._real_db = value

    def archive(self, url: str) -> None:
        self.db[_ROW_URL] = dict(_ROW)
        self.db.store()
