"""Discover content via tutubo's content-type-aware search factories.

The CLI ``discover`` subcommand picks a factory by ``--kind`` and
streams previews into the DB through :class:`YoutubeArchivist`.
"""
from __future__ import annotations

import logging
from typing import Iterable, Iterator

from tutubo.search import YoutubeSearch

from media_archivist.youtube import YoutubeArchivist

LOG = logging.getLogger("media_archivist.discover")


# Canonical kind → (factory, iterator) name pair on YoutubeSearch.
_KIND_TO_METHODS = {
    "movies":            ("for_movies",            "iterate_movies"),
    "documentaries":     ("for_documentaries",     "iterate_documentaries"),
    "short_films":       ("for_short_films",       "iterate_short_films"),
    "trailers":          ("for_trailers",          "iterate_trailers"),
    "behind_the_scenes": ("for_behind_the_scenes", "iterate_behind_the_scenes"),
    "anime":             ("for_anime",             "iterate_anime"),
    "tv_episodes":       ("for_tv_episodes",       "iterate_tv_episodes"),
    "audiobooks":        ("for_audiobooks",        "iterate_audiobooks"),
    "audio_dramas":      ("for_audio_dramas",      "iterate_audio_dramas"),
    "podcasts":          ("for_podcasts",          "iterate_podcasts"),
    "stand_up":          ("for_stand_up",          "iterate_stand_up"),
    "interviews":        ("for_interviews",        "iterate_interviews"),
    "lectures":          ("for_lectures",          "iterate_lectures"),
    "concerts":          ("for_concerts",          "iterate_concerts"),
    "tutorials":         ("for_tutorials",         "iterate_tutorials"),
    "music_videos":      ("for_music_videos",      "iterate_music_videos"),
    "music_audio":       ("for_music_audio",       "iterate_music_audio"),
    "compilations":      ("for_compilations",      "iterate_compilations"),
    "reactions":         ("for_reactions",         "iterate_reactions"),
    "news":              ("for_news",              "iterate_news"),
    "sport":             ("for_sport",             "iterate_sport"),
    "gaming":            ("for_gaming",            "iterate_gaming"),
    "kids":              ("for_kids",              "iterate_kids"),
}


def supported_kinds() -> Iterable[str]:
    return sorted(_KIND_TO_METHODS)


def _resolve(kind: str) -> tuple[str, str]:
    if kind not in _KIND_TO_METHODS:
        raise ValueError(
            f"unknown discover kind: {kind!r}; "
            f"choose from {', '.join(supported_kinds())}"
        )
    return _KIND_TO_METHODS[kind]


def discover_iterator(kind: str, query: str, *, max_results: int = -1) -> Iterator:
    """Yield raw tutubo preview / Video objects for the given kind+query."""
    factory_name, iter_name = _resolve(kind)
    search = getattr(YoutubeSearch, factory_name)(query)
    return getattr(search, iter_name)(max_res=max_results)


def discover(db_path: str, *, kind: str, query: str,
             max_results: int = -1,
             required_kwords=None,
             blacklisted_kwords=None,
             min_duration: int = -1) -> int:
    """Run a discover query and archive every result into the DB.

    Returns the number of rows attempted (whether they were archived or
    filtered out). Filtering happens inside :class:`YoutubeArchivist`.
    """
    archivist = YoutubeArchivist(
        db_path=db_path,
        required_kwords=required_kwords,
        blacklisted_kwords=blacklisted_kwords,
        min_duration=min_duration,
    )
    n = 0
    for item in discover_iterator(kind, query, max_results=max_results):
        # Some iterators yield previews with `.get()` to upgrade; archive_video
        # handles both bare Video objects and already-promoted ones.
        try:
            video = item.get() if hasattr(item, "get") else item
            archivist.archive_video(video)
            n += 1
        except Exception:
            LOG.exception("discover: failed to archive %r", item)
    return n
