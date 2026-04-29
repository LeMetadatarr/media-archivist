# Schema & validation

Every backend writes through a pydantic model. There are no untyped dicts
in the write path.

## Source enum

`media_archivist.models.Source` is the discriminator across the unified
schema:

| Value             | Backend           |
| ----------------- | ----------------- |
| `youtube`         | `YoutubeArchivist`         |
| `youtube_music`   | `YoutubeMusicArchivist`    |
| `bandcamp`        | `BandcampArchivist`        |
| `soundcloud`      | `SoundCloudArchivist`      |
| `internet_archive`| `IAArchivist`              |

## Raw entry models

Each backend has a dedicated pydantic model in
`media_archivist/models/raw.py`. Every model carries:

- `source` (literal, used as the discriminator)
- `url` (canonical entry key on disk)
- `title`, `tags`, `thumbnail`
- `extra: dict` — free-form per-archive metadata that doesn't map to a
  named field (preserved verbatim, round-trippable).

Backend-specific fields (excerpt):

| Model                    | Adds                                                                                          |
| ------------------------ | --------------------------------------------------------------------------------------------- |
| `RawYoutubeEntry`        | `videoId`, `is_live`, `published`, `views`, `description`, `duration?`, `author?`, `playlist?` |
| `RawYoutubeMusicEntry`   | `videoId`, `artist`, `album`, `year?`, `duration?`, `explicit`, `video_type`, `audio_only`, `music_video`, `views`, `playlist?`, `playlist_id?`, `album_browse_id?`, `artist_browse_id?`, `label?` |
| `RawBandcampEntry`       | `artist?`, `album`, `album_url?`, `track_number?`, `duration?`, `stream?`, `artwork?`         |
| `RawSoundcloudEntry`     | `artist?`, `artist_url?`, `duration?`, `stream?`, `source_query?`, `source_url?`              |
| `RawIAEntry`             | `collection`, `duration?` (str or float), `streams: list[str]`, `images: list[str]`           |

The discriminated union `RawEntry = Union[…]` (also exported) is what
pydantic uses to dispatch when validating an unknown row.

## Legacy v0.1 inference

Files written by `youtube_archivist` 0.0.x didn't carry a `source` field.
`media_archivist.models.parse_raw(dict)` infers it from the URL shape so
you can load any historical DB without migration:

```python
from media_archivist.models import parse_raw, Source

row = parse_raw({"url": "https://x.bandcamp.com/track/y", "title": "t"})
assert row.source is Source.BANDCAMP
```

## Strictness

Models are **lenient on construction** (every optional field has a
default) and **strict on round-trip** in tests
(`test/test_models.py` exercises every model). When a backend gains a new
field, the corresponding `Raw*Entry` must be updated — CI catches drift.

## Canonical view (v0.3, planned)

A separate `MediaEntry` model in `models/canonical.py` will project any
raw row to a unified shape (`id, source, url, title, artist?, album?,
duration?, published?, …`) for cross-source queries and dataset export.
The on-disk format does **not** change — the view is computed on read.
See [`roadmap.md`](./roadmap.md).
