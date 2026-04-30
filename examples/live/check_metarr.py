"""Live check: every metarr-backed provider in one shot.

Hits skyhook (TV), the Radarr proxy (movies), the Lidarr proxy (music
artists), OpenLibrary (books) and the BookInfo / Goodreads proxy
(books). Skips when ``metarr`` isn't installed.
"""
from __future__ import annotations

import sys

try:
    from media_archivist.providers.metarr import (
        MetarrBookInfoProvider,
        MetarrLidarrProvider,
        MetarrOpenLibraryProvider,
        MetarrRadarrProvider,
        MetarrSkyhookProvider,
    )
except ImportError:
    print("SKIP: install metarr (pip install /path/to/api_clients/metarr)",
          file=sys.stderr)
    raise SystemExit(0)


from media_archivist.models.entities import EntityKind
from media_archivist.models.signals import Medium, Signals


def main() -> int:
    if not MetarrRadarrProvider().is_available():
        print("SKIP: metarr not importable", file=sys.stderr)
        return 0
    failures: list[str] = []

    movie = MetarrRadarrProvider().lookup(
        Signals(title="Inception", medium=Medium.MOVIE))
    if movie and movie.external_ids.tmdb_movie:
        print(f"  metarr_radarr      Inception   → tmdb {movie.external_ids.tmdb_movie}")
    else:
        failures.append("metarr_radarr")

    series = MetarrSkyhookProvider().lookup(
        Signals(title="The Boys", medium=Medium.TV))
    if series and series.external_ids.tvdb:
        print(f"  metarr_skyhook     The Boys    → tvdb {series.external_ids.tvdb}")
    else:
        failures.append("metarr_skyhook")

    artist = MetarrLidarrProvider().lookup(
        Signals(title="Random", artist="Daft Punk", medium=Medium.MUSIC))
    if artist and artist.external_ids.musicbrainz_artist:
        print(f"  metarr_lidarr      Daft Punk   → mbid "
              f"{artist.external_ids.musicbrainz_artist}")
    else:
        failures.append("metarr_lidarr")

    book = MetarrOpenLibraryProvider().lookup(
        Signals(title="The Hobbit", artist="Tolkien", medium=Medium.BOOK))
    if book and book.external_ids.olid:
        authors = (book.relations or {}).get(EntityKind.AUTHOR) or []
        author_name = authors[0].name if authors else None
        print(f"  metarr_openlibrary The Hobbit  → olid "
              f"{book.external_ids.olid} (author={author_name})")
    else:
        failures.append("metarr_openlibrary")

    info = MetarrBookInfoProvider().lookup(
        Signals(title="The Hobbit", artist="Tolkien", medium=Medium.BOOK))
    if info and info.external_ids.goodreads:
        print(f"  metarr_bookinfo    The Hobbit  → goodreads "
              f"{info.external_ids.goodreads}, "
              f"isbn13 {info.external_ids.isbn_13}")
    else:
        failures.append("metarr_bookinfo")

    if failures:
        print(f"FAIL: {', '.join(failures)} returned no ids", file=sys.stderr)
        return 1
    print("PASS: skyhook + radarr-proxy + lidarr-proxy + openlibrary + "
          "bookinfo all returned ids")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
