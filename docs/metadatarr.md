# metadatarr resolver integration

`media_archivist` consumes `metadatarr.resolve` as its cross-source
metadata resolver. The provider framework, registry, signals model,
external-id model, entity layer, and ~24 built-in providers all live
in metadatarr (which in turn imports the foundation primitives from
`mediavocab`).

There are **no** `metadatarr_*` wrapper providers in
`media_archivist.providers/` — those existed pre-0.2 but were
collapsed when the resolver moved to metadatarr. Instead,
`media_archivist.providers` re-exports metadatarr's registry as-is:

```python
from media_archivist.providers import all_providers, active_providers
```

Both helpers walk the registry self-populated by
`metadatarr.resolve.providers.__init__` plus any media-archivist-
specific providers (currently just `metalarchives`).

## Built-in providers (from metadatarr)

| Provider name | Backed by | Media | External ids produced |
|---|---|---|---|
| `musicbrainz` | musicbrainz.org public API | MUSIC | `musicbrainz_recording`, `musicbrainz_release`, `musicbrainz_artist` |
| `wikidata` | wikidata.org SPARQL / wbsearch | * | `wikidata`, plus IMDb / TMDB / TVDB / MB cross-refs |
| `metadatarr` | Skyhook (`skyhook.sonarr.tv`) — Sonarr-side | EPISODIC_SERIES | `tvdb`, year |
| `audiodb` | TheAudioDB | MUSIC | `extra.audiodb_artist_id`, `extra.audiodb_album_id` |
| `tvmaze` | TVmaze public API | EPISODIC_SERIES | `extra.tvmaze` |
| `pyfanedit` | fanedit.org IFDB scraper (variant-only) | MOVIE | `fanedit_id` |
| `anilist` | AniList GraphQL | EPISODIC_SERIES + `"anime"` / COMIC + `"manga"` | `anilist_id`, plus staff / studio / character ids |
| `jikan_anime` / `jikan_manga` | MyAnimeList Jikan REST | same as AniList | `mal_id` |
| `google_books` | Google Books volumes API | BOOK / AUDIOBOOK | `google_books_id`, ISBN |
| `librivox` | librivox.org/api | AUDIOBOOK | `librivox_id` |
| `apple_podcasts` | Apple Podcasts iTunes Search | PODCAST / AUDIO_DRAMA | `apple_podcast_id` |
| `tmdb` | TMDB public API (key required) | MOVIE / EPISODIC_SERIES | `tmdb_movie`, `tmdb_tv`, `tmdb_person` |
| `arr_sonarr` / `arr_radarr` / `arr_lidarr` / `arr_readarr` | self-hosted *arr instances (URL + API key required) | EPISODIC_SERIES / MOVIE / MUSIC / BOOK | `tvdb`, `tmdb_movie`, `musicbrainz_artist`, `goodreads` |
| `discogs` | Discogs public API (optional `DISCOGS_TOKEN`) | MOVIE / MUSIC | `discogs_release`, `source_format`, `country` |
| `bluray_com` | blu-ray.com HTML scraper | MOVIE / EPISODIC_SERIES | `bluray_com_id`, `source_format="Blu-ray"` |
| `dvdcompare` | dvdcompare.net HTML scraper | MOVIE / EPISODIC_SERIES | `dvdcompare_id`, `imdb`, `edition`, `region` |
| `openlibrary` | openlibrary.org | BOOK | `olid`, `isbn_10`, `isbn_13` |
| `annas_archive` | annas-archive.org | BOOK | `extra.libgen_md5`, `isbn_13` |
| `bandcamp` | py_bandcamp scraper | MUSIC | `extra.bandcamp_album_url`, `extra.bandcamp_track_url` |
| `soundcloud` | nuvem_de_som API / HTML | MUSIC | `extra.soundcloud_track_url`, `extra.soundcloud_user_id` |
| `youtube` / `youtube_music` | tutubo (YouTube Data API + YT Music) | MUSIC / MUSIC_VIDEO / EPISODIC_SERIES | `extra.youtube_video_id`, `extra.youtube_music_video_id`, `extra.youtube_channel_id` |
| `metal_archives` (in metadatarr) | pymetal scraper | MUSIC | `metal_archives_band`, `metal_archives_release`, `metal_archives_song` |

The Skyhook / *arr provider naming is metadatarr's, not
media-archivist's — see `metadatarr.resolve.providers.servarr_proxy`
and `metadatarr.resolve.providers.arr` for the source.

## media-archivist-specific providers

None. Every resolver provider — including `metal_archives` — lives in
metadatarr. The local-source *archivist* for Encyclopaedia Metallum
(`MetalArchivesArchivist` in `media_archivist.metalarchives`) is a
different abstraction: it indexes a local metalarchives library into
the source DB, it isn't a metadata-resolver provider.

## Routing

Providers are gated by the two-axis `(media, genre_filter)` rule from
`mediavocab.MetadataProvider`:

- `media: Set[MediaType]` — the candidate's `signals.medium` must be
  in the set, OR the set is empty (universal), OR the signals do not
  declare a medium.
- `genre_filter: Set[str]` — at least one tag in
  `signals.content_genres` must overlap the filter, OR the filter is
  empty (no gate).

Anime / manga providers therefore declare e.g.
`media = {EPISODIC_SERIES, MOVIE}` plus `genre_filter = {"anime"}`
rather than a fake `MediaType.ANIME` value. Anime is a *genre* per
mediavocab spec axiom 2, not a media type.

`media_archivist.canonicalize._providers_for(providers, medium,
content_genres)` is the dispatcher.

## Configuration

Most providers self-disable via `is_available()` if their optional
upstream dependency or required API key is missing — the registry
stays consistent across environments. Set keys via env vars
documented in each provider's docstring (`TMDB_API_KEY`,
`DISCOGS_TOKEN`, `SONARR_URL` + `SONARR_API_KEY`, etc.).
