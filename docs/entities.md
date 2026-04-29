# Entities & relations

Most things you index are not just *works* — a track has an artist
(maybe several), a film has a director and a cast, a podcast has a
host, a book has an author, an album collects tracks.
`media_archivist` models these as first-class **entities** with their
own stable ids, separate from the per-work `canonical_id`.

## Identity layers

```
local row id   sha1(source:url)              — per source row
canonical_id   sha1(merged signal set)        — per *work*
entity_id      sha1(kind:dominant_external)   — per *artist / actor / album / ...*
```

Every layer is independently allocated; an entity exists whether or
not the works it appears in have been canonicalized yet.

## Entity kinds

`media_archivist.models.entities.EntityKind`:

| Kind        | Typical use                                          |
| ----------- | ---------------------------------------------------- |
| `artist`    | Music performers, recording artists, channel hosts.  |
| `album`     | A grouping of tracks; carries its own external ids.  |
| `label`     | Record labels, publishing imprints.                  |
| `channel`   | A YouTube / Bandcamp / SoundCloud profile.           |
| `actor`     | Cast member of a film / show / drama.                |
| `director`  | Director or showrunner.                              |
| `producer`  | Producer credit.                                     |
| `composer`  | Music composer (film score, song co-write).          |
| `writer`    | Screenwriter, lyricist.                              |
| `narrator`  | Audiobook / documentary narrator.                    |
| `host`      | Podcast / interview host.                            |
| `author`    | Book author.                                         |
| `other`     | Anything else; preserved verbatim.                   |

## Relations on a `CanonicalRecord`

Each `CanonicalRecord.relations` is a `dict[role, list[entity_id]]`.

```json
{
  "canonical_id": "…",
  "signals": { "title": "Tenet", "year": 2020, "country": "US", "medium": "movie" },
  "external_ids": { "imdb": "tt6723592", "tmdb_movie": 577922 },
  "relations": {
    "director": ["e_xxx"],
    "actor":    ["e_yyy", "e_zzz", "e_aaa"]
  },
  "members": ["row_id_1", "row_id_2"]
}
```

## Sidecar layout

```
<db>.json                 raw rows
<db>.canonical.json       work records (gain `relations`)
<db>.quarantine.json      pending review
<db>.entities.json        entities (NEW)
<db>.links.json           fingerprint clusters
<db>.tasks.json           server scheduler state
```

`<db>.entities.json` shape:

```json
{
  "version": 1,
  "entities": {
    "<entity_id>": {
      "id": "<entity_id>",
      "kind": "artist",
      "name": "Aphex Twin",
      "aliases": ["AFX", "aphex twin"],
      "external_ids": {
        "musicbrainz_artist": "f22942a1-…",
        "wikidata":           "Q23874"
      },
      "members": [],
      "works":   ["<canonical_id>", "<canonical_id>"],
      "first_seen": "…",
      "last_updated": "…"
    }
  }
}
```

## Allocation rule

`allocate_entity_id(kind, name, external_ids)`:

1. If the candidate has any *dominant external id* for its kind, the
   `entity_id` is `sha1("<kind>|ext:<dominant>")`. Two providers
   reporting the same MBID always converge.
2. Else, `entity_id = sha1("<kind>|name:<normalized>")`. Same name
   collapses to one entity; the resulting record's `external_ids`
   accumulate as more providers chime in.

The "dominant external id" is per-kind, defined in
`media_archivist.models.entities._dominant_external_id`. For artists
that's MusicBrainz first, then Wikidata, then TMDB / IMDb person
ids; for actors / directors / producers it's TMDB person id first,
then IMDb, then Wikidata. The point is to pick the most stable
identifier each ecosystem owns.

## Provider contract

`ProviderMatch.relations` is `dict[Role, list[ProviderEntity]]`.
Providers populate roles they actually return:

| Provider         | Roles populated                                  |
| ---------------- | ------------------------------------------------ |
| `musicbrainz`    | `artist`, `album`                                |
| `tmdb` (movie)   | `actor` (top 20), `director`, `producer`, `writer`, `composer` |
| `tmdb` (tv)      | `actor` (top 20), `director` (creators), `producer`, `writer`, `composer` |
| `arr_radarr`     | (delegates to tmdb)                              |
| `arr_sonarr`     | (delegates to tmdb)                              |
| `arr_lidarr`     | `artist`, `album`                                |
| `arr_readarr`    | `author`                                         |
| `wikidata`       | (currently work-level only; cross-refs go into `external_ids`) |

Third-party providers extend the same shape — see
[`docs/disambiguation.md`](./disambiguation.md) for the
`MetadataProvider` contract.

## Querying

The canonical view exposes both the resolved names (`relations`) and
raw ids (`relation_ids`):

```python
from media_archivist import Index

idx = Index("./music.json")
for e in idx.view(where='"Aphex Twin" in relations.artist'):
    print(e.url, e.relations)
```

`--where` supports dotted access against any dict-valued field on the
view: `relations.artist`, `relations.director`, `external_ids.imdb`,
`external_ids.musicbrainz_artist`. Method calls on strings (e.g.
`title.upper()`) remain forbidden.

## CLI

```bash
# After running canonicalize (which populates the entity sidecar)…

# Dump every artist:
media-archivist entities-list --db-file talks.json --kind artist

# Show one entity plus the works it appears in:
media-archivist entities-show --db-file talks.json \
    --entity-id 0f01848834a94cfd27e8b259ef32fa8856215854

# Counts per kind + total works each kind appears in:
media-archivist entities-stats --db-file talks.json
```

## Common queries

```bash
# Every work whose director is X:
media-archivist list --db-file films.json --canonical \
    --where '"Christopher Nolan" in relations.director'

# Every track on a labelled album:
media-archivist list --db-file songs.json --canonical \
    --where 'len(relations.album) > 0'

# Cross-source dedup that ignores the work-level fingerprint and
# instead asks: "what artists do we have, and how many tracks each?"
media-archivist entities-stats --db-file songs.json
```

## Verification

`test/test_entities.py` covers the full surface (11 tests, fully
offline via stub providers): allocation rule, sidecar round-trip,
`upsert_entity` alias merging, idempotent `attach_work`,
canonicalize populates entities, two providers sharing an MBID
converge, `Index.view()` resolves names, `--where` dotted access
works on relations and rejects string-method calls.
