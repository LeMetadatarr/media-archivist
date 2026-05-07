# Anime, manga, and books providers

This page covers the zero-setup providers for anime, manga, and supplementary
book lookups: **AniList**, **Jikan (MAL)**, and **Google Books**.

All three are always-on (`is_available()` returns `True`); they require no API
key, no environment variables, and no self-hosted services.

---

## AniList (`anilist`)

**Source**: [anilist.co](https://anilist.co) — GraphQL API, 90 req/min, no key.

**Media**: `MediaType.EPISODIC_SERIES (with content_genres=["anime"])`, `MediaType.COMIC + content_genres=["manga"]`

### What it returns

| Field | Notes |
|---|---|
| `signals.title` | English title preferred; falls back to romaji |
| `signals.year` | `startDate.year` |
| `signals.medium` | `ANIME` or `MANGA` depending on query type |
| `external_ids.anilist_id` | AniList numeric media id |
| `external_ids.extra["title_romaji"]` | Romanized title when different from English |
| `external_ids.extra["title_native"]` | Native script title |
| `relations[DIRECTOR]` | Staff edges with role `"Director"` |
| `relations[AUTHOR]` | Staff edges with roles `"Original Creator"`, `"Original Story"`, `"Story"` |
| `relations[WRITER]` | Staff edges with roles `"Series Composition"`, `"Script"` |
| `relations[COMPOSER]` | Staff edges with role `"Music"` |
| `relations[STUDIO]` | Main production studio; `extra["anilist_studio_id"]` |

Confidence: **0.90**

### Example

```python
from metadatarr.resolve.providers.anilist import AniListProvider
from mediavocab import Signals

p = AniListProvider()
m = p.lookup(Signals(title="Cowboy Bebop", medium=MediaType.EPISODIC_SERIES (with content_genres=["anime"])))
print(m.external_ids.anilist_id)          # 1
print(m.signals.year)                     # 1998
print(m.relations["director"][0].name)    # Shinichirou Watanabe
print(m.relations["studio"][0].name)      # Sunrise
```

---

## Jikan — anime (`jikan_anime`) and manga (`jikan_manga`)

**Source**: [api.jikan.moe](https://docs.api.jikan.moe/) — unofficial MAL REST
proxy, 3 req/s / 60 req/min, no key.

**Media**: `jikan_anime` → `MediaType.EPISODIC_SERIES (with content_genres=["anime"])`; `jikan_manga` → `MediaType.COMIC + content_genres=["manga"]`

Jikan mirrors MyAnimeList data, the largest community anime/manga database.
Use it alongside AniList: both providers run concurrently and the canonicalizer
merges their results.

### What it returns

| Field | Notes |
|---|---|
| `signals.title` | `title_english` preferred; falls back to `title` |
| `signals.year` | Extracted from `aired.prop.from.year` (anime) or `published.prop.from.year` (manga) |
| `external_ids.mal_id` | MyAnimeList numeric id |
| `external_ids.extra["title_japanese"]` | Japanese title in native script |
| `relations[STUDIO]` | First studio from `studios[]`; `extra["mal_studio_id"]` |
| `relations[AUTHOR]` | Manga only — `authors[]`; MAL `"Last, First"` order is flipped to `"First Last"` |

Confidence: **0.85**

Year filtering: when `signals.year` is set, results within ±1 year are
preferred over the raw top result.

### Example

```python
from metadatarr.resolve.providers.jikan import JikanAnimeProvider, JikanMangaProvider
from mediavocab import Signals

anime = JikanAnimeProvider()
m = anime.lookup(Signals(title="Cowboy Bebop", medium=MediaType.EPISODIC_SERIES (with content_genres=["anime"])))
print(m.external_ids.mal_id)              # 1
print(m.external_ids.extra["title_japanese"])  # カウボーイビバップ

manga = JikanMangaProvider()
m2 = manga.lookup(Signals(title="Berserk", medium=MediaType.COMIC, content_genres=["manga"]))
print(m2.relations["author"][0].name)     # Kentarou Miura
```

---

## Google Books (`google_books`)

**Source**: [googleapis.com/books/v1](https://developers.google.com/books/docs/v1/using)
— REST, no key required for up to 1,000 queries/day.

**Media**: `MediaType.BOOK`, `MediaType.AUDIOBOOK`

Use Google Books as a secondary book provider alongside OpenLibrary and
Goodreads. Its main strength is ISBN resolution for editions that OpenLibrary
doesn't index.

### What it returns

| Field | Notes |
|---|---|
| `signals.title` | `volumeInfo.title` |
| `signals.year` | First 4 digits of `publishedDate` |
| `signals.language` | `volumeInfo.language` (ISO 639-1) |
| `external_ids.google_books_id` | Volume id (alphanumeric, e.g. `"UGmrEAAAQBAJ"`) |
| `external_ids.isbn_13` | From `industryIdentifiers` |
| `external_ids.isbn_10` | From `industryIdentifiers` |
| `relations[AUTHOR]` | One entity per name in `volumeInfo.authors` |

Confidence: **0.80**

Query format: `intitle:{title}` + optional `+inauthor:{artist}`. When
`signals.year` is provided, results within ±1 year are preferred.

### Example

```python
from metadatarr.resolve.providers.google_books import GoogleBooksProvider
from mediavocab import Signals

p = GoogleBooksProvider()
m = p.lookup(Signals(title="The Hobbit", artist="Tolkien", medium=MediaType.BOOK))
print(m.external_ids.google_books_id)    # e.g. "UGmrEAAAQBAJ"
print(m.external_ids.isbn_13)            # 9780261102217
print(m.relations["author"][0].name)     # J.R.R. Tolkien
```

---

## Pairing recommendations

For anime/manga entries already indexed from YouTube or Internet Archive:

```python
# canonicalize with AniList + Jikan together
media-archivist canonicalize --db-file anime.json \
    --providers anilist,jikan_anime,wikidata
```

The canonicalizer runs both AniList and Jikan concurrently. They produce
complementary IDs (`anilist_id` + `mal_id`) on the same canonical record —
no conflict, just additive enrichment.

For a book-heavy database, stack all book providers:

```bash
media-archivist canonicalize --db-file books.json \
    --providers openlibrary --providers google_books \
    --providers annas_archive
```

Google Books fills ISBN gaps that OpenLibrary misses; Anna's Archive adds
`libgen_md5` and additional ISBN-13 coverage.

---

## Not yet implemented (see README roadmap)

| Provider | Blocker |
|---|---|
| OpenCritic (`game`) | RapidAPI key required — originally assumed keyless |
| RAWG (`game`) | Free key required |
| Audnexus (`audiobook`) | Lookup-only (ASIN), no search endpoint |
| Trakt (`movie`, `tv`) | API key required for all endpoints |
| AniDB (`anime`) | Throttled without registration; scraping brittle |
