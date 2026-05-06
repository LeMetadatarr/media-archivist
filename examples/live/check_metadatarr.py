"""Live check: metadatarr-backed providers.

Looks up arr_radarr (movies), arr_sonarr / metadatarr (TV), arr_lidarr
(music), openlibrary (books), and annas_archive (books) via the provider
registry. Skips any provider that is not available (missing API key / URL).
Skips entirely when ``metadatarr`` is not installed.
"""
from __future__ import annotations

import sys

try:
    import metadatarr.resolve.providers  # noqa: F401 — triggers self-registration
except ImportError:
    print("SKIP: install metadatarr (pip install metadatarr)", file=sys.stderr)
    raise SystemExit(0)

from mediavocab import MediaType
from mediavocab.models.signals import Signals
from metadatarr.resolve.entities import EntityRole
from media_archivist.providers import all_providers


def _check(name: str, signals: Signals, id_field: str,
           failures: list[str]) -> None:
    registry = all_providers()
    provider = registry.get(name)
    if provider is None or not provider.is_available():
        print(f"  {name:<22} SKIP (not available)")
        return
    result = provider.lookup(signals)
    if result is None:
        value = None
    else:
        value = getattr(result.external_ids, id_field, None)
        if value is None:
            value = (result.external_ids.extra or {}).get(id_field)
    if value:
        print(f"  {name:<22} → {id_field}={value}")
    else:
        print(f"  {name:<22} FAIL (no {id_field})", file=sys.stderr)
        failures.append(name)


def main() -> int:
    failures: list[str] = []

    _check("arr_radarr",   Signals(title="Inception",  medium=MediaType.MOVIE),
           "tmdb_movie",   failures)
    _check("skyhook",   Signals(title="The Boys",   medium=MediaType.EPISODIC_SERIES),
           "tvdb",         failures)
    _check("arr_lidarr",   Signals(title="Random Access Memories", artist="Daft Punk",
                                   medium=MediaType.MUSIC),
           "musicbrainz_artist", failures)
    _check("openlibrary",  Signals(title="The Hobbit", artist="Tolkien",
                                   medium=MediaType.BOOK),
           "olid",         failures)
    # annas_archive populates extra["annas_archive_md5"] not a typed isbn field
    _check("annas_archive", Signals(title="The Hobbit", artist="Tolkien",
                                    medium=MediaType.BOOK),
           "annas_archive_md5", failures)

    if failures:
        print(f"FAIL: {', '.join(failures)}", file=sys.stderr)
        return 1
    print("PASS: all available metadatarr-backed providers returned ids")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
