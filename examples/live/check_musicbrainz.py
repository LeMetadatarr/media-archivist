"""Live check: MusicBrainz provider returns a recording id for a known track."""
from __future__ import annotations

import sys

from mediavocab.models.signals import Signals
from media_archivist.providers import all_providers


def main() -> int:
    mb = all_providers().get("musicbrainz")
    if mb is None:
        print("FAIL: musicbrainz provider not registered", file=sys.stderr)
        return 1
    sig = Signals(title="Bohemian Rhapsody", artist="Queen")
    match = mb.lookup(sig)
    if match is None:
        print("FAIL: musicbrainz returned no match", file=sys.stderr)
        return 1
    if not match.external_ids.musicbrainz_recording:
        print(f"FAIL: musicbrainz match without recording mbid: {match}",
              file=sys.stderr)
        return 1
    print(f"PASS: mbid {match.external_ids.musicbrainz_recording} "
          f"(confidence {match.confidence:.2f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
