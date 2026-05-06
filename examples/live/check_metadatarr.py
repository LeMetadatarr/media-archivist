"""Live check: every metadatarr-backed provider in one shot.

Hits skyhook (TV), the Radarr proxy (movies), the Lidarr proxy (music
artists), OpenLibrary (books) and the BookInfo / Goodreads proxy
(books). Skips when ``metadatarr`` isn't installed.
"""
from __future__ import annotations

import sys

try:
    from media_archivist.providers.metadatarr import (
        MetadatarrBookInfoProvider,
        MetadatarrLidarrProvider,
        MetadatarrOpenLibraryProvider,
        MetadatarrRadarrProvider,
        MetadatarrSkyhookProvider,
    )
except ImportError:
    print("SKIP: install metadatarr (pip install /path/to/api_clients/metadatarr)",
          file=sys.stderr)
    raise SystemExit(0)


from mediavocab import MediaType
from metadatarr.resolve.entities import EntityRole
from mediavocab.models.signals import Signals


def main() -> int:
    if not MetadatarrRadarrProvider().is_available():
        print("SKIP: metadatarr not importable", file=sys.stderr)
        return 0
    failures: list[str] = []

    movie = MetadatarrRadarrProvider().lookup(
        Signals(title="Inception", medium=MediaType.MOVIE))
    if movie and movie.external_ids.tmdb_movie:
        print(f"  metadatarr_radarr      Inception   → tmdb {movie.external_ids.tmdb_movie}")
    else:
        failures.append("metadatarr_radarr")

    series = MetadatarrSkyhookProvider().lookup(
        Signals(title="The Boys", medium=MediaType.EPISODIC_SERIES))
    if series and series.external_ids.tvdb:
        print(f"  metadatarr_skyhook     The Boys    → tvdb {series.external_ids.tvdb}")
    else:
        failures.append("metadatarr_skyhook")

    artist = MetadatarrLidarrProvider().lookup(
        Signals(title="Random", artist="Daft Punk", medium=MediaType.MUSIC))
    if artist and artist.external_ids.musicbrainz_artist:
        print(f"  metadatarr_lidarr      Daft Punk   → mbid "
              f"{artist.external_ids.musicbrainz_artist}")
    else:
        failures.append("metadatarr_lidarr")

    book = MetadatarrOpenLibraryProvider().lookup(
        Signals(title="The Hobbit", artist="Tolkien", medium=MediaType.BOOK))
    if book and book.external_ids.olid:
        authors = (book.relations or {}).get(EntityRole.AUTHOR) or []
        author_name = authors[0].name if authors else None
        print(f"  metadatarr_openlibrary The Hobbit  → olid "
              f"{book.external_ids.olid} (author={author_name})")
    else:
        failures.append("metadatarr_openlibrary")

    info = MetadatarrBookInfoProvider().lookup(
        Signals(title="The Hobbit", artist="Tolkien", medium=MediaType.BOOK))
    if info and info.external_ids.goodreads:
        print(f"  metadatarr_bookinfo    The Hobbit  → goodreads "
              f"{info.external_ids.goodreads}, "
              f"isbn13 {info.external_ids.isbn_13}")
    else:
        failures.append("metadatarr_bookinfo")

    if failures:
        print(f"FAIL: {', '.join(failures)} returned no ids", file=sys.stderr)
        return 1
    print("PASS: skyhook + radarr-proxy + lidarr-proxy + openlibrary + "
          "bookinfo all returned ids")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
