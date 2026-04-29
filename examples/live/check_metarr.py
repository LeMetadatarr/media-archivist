"""Live check: Servarr metadata proxies via metarr.

Hits skyhook (tv), the Radarr proxy (movies), and the Lidarr proxy
(artists). PASS when all three return real ids; SKIP when metarr isn't
installed.
"""
from __future__ import annotations

import sys

try:
    from media_archivist.providers.metarr import MetarrProvider
except ImportError:
    print("SKIP: install metarr (pip install /path/to/api_clients/metarr)",
          file=sys.stderr)
    raise SystemExit(0)


from media_archivist.models.signals import Medium, Signals


def main() -> int:
    p = MetarrProvider()
    if not p.is_available():
        print("SKIP: metarr not importable", file=sys.stderr)
        return 0
    failures: list[str] = []

    movie = p.lookup(Signals(title="Inception", medium=Medium.MOVIE))
    if movie and movie.external_ids.tmdb_movie:
        print(f"  movie  Inception → tmdb {movie.external_ids.tmdb_movie}")
    else:
        failures.append("movie")

    series = p.lookup(Signals(title="The Boys", medium=Medium.TV))
    if series and series.external_ids.tvdb:
        print(f"  tv     The Boys → tvdb {series.external_ids.tvdb}")
    else:
        failures.append("tv")

    artist = p.lookup(Signals(title="Random", artist="Daft Punk",
                              medium=Medium.MUSIC))
    if artist and artist.external_ids.musicbrainz_artist:
        print(f"  music  Daft Punk → mbid {artist.external_ids.musicbrainz_artist}")
    else:
        failures.append("music")

    if failures:
        print(f"FAIL: {', '.join(failures)} returned no ids", file=sys.stderr)
        return 1
    print("PASS: skyhook + radarr-proxy + lidarr-proxy all returned ids")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
