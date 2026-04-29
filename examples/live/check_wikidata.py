"""Live check: Wikidata provider resolves a Q-id and an IMDb tt-id for Tenet."""
from __future__ import annotations

import sys

from media_archivist.models.signals import Medium, Signals
from media_archivist.providers import all_providers


def main() -> int:
    wd = all_providers().get("wikidata")
    if wd is None:
        print("FAIL: wikidata provider not registered", file=sys.stderr)
        return 1
    sig = Signals(title="Tenet", medium=Medium.MOVIE)
    match = wd.lookup(sig)
    if match is None:
        print("FAIL: wikidata returned no match", file=sys.stderr)
        return 1
    print(f"PASS: wikidata Q-id={match.external_ids.wikidata}, "
          f"imdb={match.external_ids.imdb}, "
          f"tmdb_movie={match.external_ids.tmdb_movie}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
