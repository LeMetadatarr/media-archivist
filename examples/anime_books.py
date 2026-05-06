"""Offline example — anime, manga, and book provider shapes.

Demonstrates AniListProvider, JikanAnimeProvider, JikanMangaProvider,
and GoogleBooksProvider using stub data so the script runs without
touching any live API.
"""
from __future__ import annotations

from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Shared stub transport
# ---------------------------------------------------------------------------

def _make_json_transport(payload: dict):
    """Return a fake httpx.get / httpx.post that always returns *payload*."""
    try:
        import httpx
    except ImportError:
        raise SystemExit("httpx is required: pip install httpx")

    def _fake_get(url, *, params=None, headers=None, timeout=None, **kw):
        import json
        return httpx.Response(200,
                              content=json.dumps(payload).encode(),
                              headers={"content-type": "application/json"},
                              request=httpx.Request("GET", url))

    def _fake_post(url, *, json=None, timeout=None, **kw):
        import json as _json
        return httpx.Response(200,
                              content=_json.dumps(payload).encode(),
                              headers={"content-type": "application/json"},
                              request=httpx.Request("POST", url))

    mock = MagicMock()
    mock.get = _fake_get
    mock.post = _fake_post
    return mock


# ===========================================================================
# 1. AniList — anime lookup
# ===========================================================================

print("=" * 60)
print("1. AniListProvider — anime (Cowboy Bebop)")
print("=" * 60)

import metadatarr.resolve.providers.anilist as _al_mod

_al_mod.httpx = _make_json_transport({
    "data": {
        "Media": {
            "id": 1,
            "title": {"romaji": "Cowboy Bebop", "english": "Cowboy Bebop",
                      "native": "カウボーイビバップ"},
            "startDate": {"year": 1998},
            "endDate": {"year": 1999},
            "episodes": 26,
            "chapters": None, "volumes": None,
            "status": "FINISHED",
            "format": "TV",
            "genres": ["Action", "Sci-Fi"],
            "staff": {
                "edges": [
                    {"role": "Director",
                     "node": {"id": 97009, "name": {"full": "Shinichirou Watanabe"}}},
                    {"role": "Music",
                     "node": {"id": 96081, "name": {"full": "Yoko Kanno"}}},
                    {"role": "Original Creator",
                     "node": {"id": 97197, "name": {"full": "Hajime Yatate"}}},
                ]
            },
            "studios": {"nodes": [{"id": 14, "name": "Sunrise"}]},
        }
    }
})

from mediavocab import MediaType
from metadatarr.resolve.providers.anilist import AniListProvider
from mediavocab.models.signals import Signals
from metadatarr.resolve.entities import EntityKind

m = AniListProvider().lookup(Signals(title="Cowboy Bebop", medium=MediaType.EPISODIC_SERIES, content_genres=["anime"]))
assert m is not None
print(f"  title        : {m.signals.title}")
print(f"  year         : {m.signals.year}")
print(f"  anilist_id   : {m.external_ids.anilist_id}")
print(f"  title_native : {m.external_ids.extra.get('title_native')}")
print(f"  director     : {m.relations[EntityKind.DIRECTOR][0].name}")
print(f"  composer     : {m.relations[EntityKind.COMPOSER][0].name}")
print(f"  studio       : {m.relations[EntityKind.STUDIO][0].name}")
print(f"  confidence   : {m.confidence}")

# ===========================================================================
# 2. AniList — manga lookup
# ===========================================================================

print()
print("=" * 60)
print("2. AniListProvider — manga (Berserk)")
print("=" * 60)

_al_mod.httpx = _make_json_transport({
    "data": {
        "Media": {
            "id": 30002,
            "title": {"romaji": "Berserk", "english": "Berserk",
                      "native": "ベルセルク"},
            "startDate": {"year": 1989},
            "endDate": {"year": 2023},
            "episodes": None,
            "chapters": 364, "volumes": 41,
            "status": "FINISHED",
            "format": "MANGA",
            "genres": ["Action", "Adventure", "Drama", "Fantasy"],
            "staff": {
                "edges": [
                    {"role": "Story & Art",
                     "node": {"id": 98001, "name": {"full": "Kentarou Miura"}}},
                ]
            },
            "studios": {"nodes": []},
        }
    }
})

m2 = AniListProvider().lookup(Signals(title="Berserk", medium=MediaType.COMIC, content_genres=["manga"]))
assert m2 is not None
print(f"  title      : {m2.signals.title}")
print(f"  year       : {m2.signals.year}")
print(f"  anilist_id : {m2.external_ids.anilist_id}")
print(f"  medium     : {m2.signals.medium.value}")

# ===========================================================================
# 3. Jikan — anime lookup
# ===========================================================================

print()
print("=" * 60)
print("3. JikanAnimeProvider — anime (Cowboy Bebop)")
print("=" * 60)

import metadatarr.resolve.providers.jikan as _jikan_mod

_jikan_mod.httpx = _make_json_transport({
    "data": [
        {
            "mal_id": 1,
            "title": "Cowboy Bebop",
            "title_english": "Cowboy Bebop",
            "title_japanese": "カウボーイビバップ",
            "type": "TV",
            "episodes": 26,
            "aired": {
                "prop": {"from": {"year": 1998, "month": 4, "day": 3}}
            },
            "score": 8.75,
            "studios": [{"mal_id": 42, "name": "Sunrise"}],
        }
    ]
})

from metadatarr.resolve.providers.jikan import JikanAnimeProvider, JikanMangaProvider

m3 = JikanAnimeProvider().lookup(Signals(title="Cowboy Bebop", medium=MediaType.EPISODIC_SERIES, content_genres=["anime"]))
assert m3 is not None
print(f"  title            : {m3.signals.title}")
print(f"  mal_id           : {m3.external_ids.mal_id}")
print(f"  year             : {m3.signals.year}")
print(f"  title_japanese   : {m3.external_ids.extra.get('title_japanese')}")
print(f"  studio           : {m3.relations[EntityKind.STUDIO][0].name}")

# ===========================================================================
# 4. Jikan — manga lookup + author name flip
# ===========================================================================

print()
print("=" * 60)
print("4. JikanMangaProvider — manga (Berserk) + author name flip")
print("=" * 60)

_jikan_mod.httpx = _make_json_transport({
    "data": [
        {
            "mal_id": 2,
            "title": "Berserk",
            "title_english": "Berserk",
            "title_japanese": "ベルセルク",
            "type": "Manga",
            "chapters": 364,
            "published": {
                "prop": {"from": {"year": 1989, "month": 11, "day": 26}}
            },
            "authors": [
                {"mal_id": 1868, "name": "Miura, Kentarou", "type": "person"}
            ],
        }
    ]
})

m4 = JikanMangaProvider().lookup(Signals(title="Berserk", medium=MediaType.COMIC, content_genres=["manga"]))
assert m4 is not None
author = m4.relations[EntityKind.AUTHOR][0]
print(f"  title            : {m4.signals.title}")
print(f"  mal_id           : {m4.external_ids.mal_id}")
print(f"  author (flipped) : {author.name}")   # "Kentarou Miura", not "Miura, Kentarou"
print(f"  mal_person_id    : {author.external_ids.mal_person_id}")

# ===========================================================================
# 5. Google Books — book lookup with ISBN + author
# ===========================================================================

print()
print("=" * 60)
print("5. GoogleBooksProvider — book (The Hobbit)")
print("=" * 60)

import metadatarr.resolve.providers.google_books as _gb_mod

_gb_mod.httpx = _make_json_transport({
    "totalItems": 842,
    "items": [
        {
            "id": "UGmrEAAAQBAJ",
            "volumeInfo": {
                "title": "The Hobbit",
                "authors": ["J.R.R. Tolkien"],
                "publishedDate": "1937-09-21",
                "industryIdentifiers": [
                    {"type": "ISBN_13", "identifier": "9780261102217"},
                    {"type": "ISBN_10", "identifier": "0261102214"},
                ],
                "language": "en",
            }
        }
    ]
})

from metadatarr.resolve.providers.google_books import GoogleBooksProvider

m5 = GoogleBooksProvider().lookup(Signals(title="The Hobbit", artist="Tolkien",
                                          medium=MediaType.BOOK))
assert m5 is not None
print(f"  title             : {m5.signals.title}")
print(f"  google_books_id   : {m5.external_ids.google_books_id}")
print(f"  isbn_13           : {m5.external_ids.isbn_13}")
print(f"  isbn_10           : {m5.external_ids.isbn_10}")
print(f"  language          : {m5.signals.language}")
print(f"  year              : {m5.signals.year}")
print(f"  author            : {m5.relations[EntityKind.AUTHOR][0].name}")
print(f"  confidence        : {m5.confidence}")

# ===========================================================================
# 6. MediaType-guard examples
# ===========================================================================

print()
print("=" * 60)
print("6. MediaType guards — wrong medium returns None")
print("=" * 60)

from metadatarr.resolve.providers.anilist import AniListProvider
from metadatarr.resolve.providers.google_books import GoogleBooksProvider

assert AniListProvider().lookup(Signals(title="X", medium=MediaType.MOVIE)) is None
assert JikanAnimeProvider().lookup(Signals(title="X", medium=MediaType.COMIC, content_genres=["manga"])) is None
assert JikanMangaProvider().lookup(Signals(title="X", medium=MediaType.EPISODIC_SERIES, content_genres=["anime"])) is None
assert GoogleBooksProvider().lookup(Signals(title="X", medium=MediaType.EPISODIC_SERIES, content_genres=["anime"])) is None
print("  All None as expected ✓")

print()
print("All examples passed.")
