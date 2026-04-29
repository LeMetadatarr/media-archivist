"""MusicBrainz provider — free, no API key, but rate-limited (1 req/s)."""
from __future__ import annotations

import logging
from typing import Optional

import requests

from media_archivist.models.external_ids import ExternalIds
from media_archivist.models.signals import Medium, Signals
from media_archivist.providers.base import (
    MetadataProvider,
    ProviderMatch,
    register,
)

LOG = logging.getLogger("media_archivist.providers.musicbrainz")
_BASE = "https://musicbrainz.org/ws/2"
_UA = "media_archivist/0.1 ( https://github.com/TigreGotico/media-archivist )"


class MusicBrainzProvider(MetadataProvider):
    name = "musicbrainz"
    media = {Medium.MUSIC}

    def is_available(self) -> bool:
        return True

    def lookup(self, signals: Signals) -> Optional[ProviderMatch]:
        if not (signals.title and signals.artist):
            return None
        if signals.medium and signals.medium != Medium.MUSIC:
            return None
        params = {
            "query": f'recording:"{signals.title}" AND artist:"{signals.artist}"',
            "fmt": "json",
            "limit": 5,
        }
        try:
            resp = requests.get(f"{_BASE}/recording", params=params,
                                headers={"User-Agent": _UA}, timeout=20)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            LOG.warning("MusicBrainz lookup failed: %s", e)
            return None

        recordings = data.get("recordings") or []
        if not recordings:
            return None
        # MB returns results sorted by score (0–100). Take the top hit.
        top = recordings[0]
        confidence = float(top.get("score", 0)) / 100.0

        artist_credit = top.get("artist-credit") or []
        artist_name = artist_credit[0].get("name") if artist_credit else None
        artist_mbid = (artist_credit[0].get("artist") or {}).get("id") if artist_credit else None

        release = (top.get("releases") or [{}])[0]
        country = release.get("country")
        date = release.get("date") or top.get("first-release-date")
        year = int(date[:4]) if date and len(date) >= 4 and date[:4].isdigit() else None

        runtime_ms = top.get("length")
        runtime_s = float(runtime_ms) / 1000 if isinstance(runtime_ms, (int, float)) else None

        return ProviderMatch(
            provider=self.name,
            confidence=confidence,
            signals=Signals(
                title=top.get("title"),
                artist=artist_name,
                year=year,
                country=country,
                runtime=runtime_s,
                medium=Medium.MUSIC,
            ),
            external_ids=ExternalIds(
                musicbrainz_recording=top.get("id"),
                musicbrainz_release=release.get("id") or None,
                musicbrainz_artist=artist_mbid,
            ),
        )


register(MusicBrainzProvider())
