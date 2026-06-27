# metadatarr resolver integration

`media_archivist` consumes `metadatarr.resolve` as its cross-source
metadata resolver. The provider framework, registry, signals model,
external-id model, entity layer, and ~22 built-in providers all live
in metadatarr (which in turn imports the foundation primitives from
`mediavocab`).

`media_archivist.providers` re-exports metadatarr's registry as-is:

```python
from media_archivist.providers import all_providers, active_providers
```

Both helpers walk the registry self-populated by
`metadatarr.resolve.providers.__init__`. There are no
media-archivist-specific providers.

## Built-in providers (from metadatarr)

| Provider name | Backed by | Media | Modality | External ids produced |
|---|---|---|---|---|
| `musicbrainz` | musicbrainz.org public API | MUSIC | * | `musicbrainz_recording`, `musicbrainz_release`, `musicbrainz_artist` |
| `wikidata` | wikidata.org SPARQL / wbsearch | * | * | `wikidata`, plus IMDb / TMDB / TVDB / MB cross-refs |
| `skyhook` | Servarr public proxies (no key needed): skyhook / radarr / lidarr / OpenLibrary | MOVIE, EPISODIC_SERIES, MUSIC, BOOK | * | `tvdb`, `tmdb_movie`, `tmdb_tv`, `musicbrainz_artist`, `olid` |
| `audiodb` | TheAudioDB | MUSIC | AUDIO | `extra.audiodb_artist_id`, `extra.audiodb_album_id` |
| `tvmaze` | TVmaze public API | EPISODIC_SERIES | * | `extra.tvmaze` |
| `pyfanedit` | fanedit.org IFDB scraper (variant-only) | MOVIE | VIDEO | `fanedit_id` |
| `anilist` | AniList GraphQL | EPISODIC_SERIES + `"anime"` / COMIC + `"manga"` | VIDEO, TEXT | `anilist_id`, plus staff / studio / character ids |
| `jikan_anime` / `jikan_manga` | MyAnimeList Jikan REST | same as AniList | VIDEO / TEXT | `mal_id` |
| `librivox` | librivox.org/api | AUDIOBOOK | AUDIO | `librivox_id` |
| `apple_podcasts` | Apple Podcasts iTunes Search | PODCAST / AUDIO_DRAMA | AUDIO | `apple_podcast_id` |
| `discogs` | Discogs public API (optional `DISCOGS_TOKEN`) | MOVIE / MUSIC | AUDIO, VIDEO | `discogs_release`, `source_format`, `country` |
| `bluray_com` | blu-ray.com HTML scraper | MOVIE / EPISODIC_SERIES | * | `bluray_com_id`, `source_format="Blu-ray"` |
| `dvdcompare` | dvdcompare.net HTML scraper | MOVIE / EPISODIC_SERIES | VIDEO | `dvdcompare_id`, `imdb`, `edition`, `region` |
| `openlibrary` | openlibrary.org | BOOK | * | `olid`, `isbn_10`, `isbn_13` |
| `annas_archive` | annas-archive.org | BOOK | TEXT | `extra.libgen_md5`, `isbn_13` |
| `bandcamp` | py_bandcamp scraper | MUSIC | AUDIO | `extra.bandcamp_album_url`, `extra.bandcamp_track_url` |
| `soundcloud` | nuvem_de_som API / HTML | MUSIC | AUDIO | `extra.soundcloud_track_url`, `extra.soundcloud_user_id` |
| `youtube` / `youtube_music` | tutubo (YouTube Data API + YT Music) | MUSIC / MUSIC_VIDEO / EPISODIC_SERIES | * / AUDIO | `extra.youtube_video_id`, `extra.youtube_music_video_id`, `extra.youtube_channel_id` |
| `metal_archives` | pymetal scraper | MUSIC | * | `metal_archives_band`, `metal_archives_release`, `metal_archives_song` |

`*` in Modality means the provider's `modality` set is empty — it accepts requests with any or no modality declared.

Provider source: `metadatarr/resolve/providers/` — `servarr_proxy.py`, etc.
There is no standalone `tmdb` provider; TMDB-shaped data for movies / series comes
through `skyhook` (`ServarrProxyProvider`) — `metadatarr/resolve/providers/servarr_proxy.py:30`.

## Routing

Providers are gated by the **three-axis** `(media, modality, genre_filter)` rule
declared on `metadatarr.resolve.base.MetadataProvider` — `metadatarr/resolve/base.py:82`:

- `media: ClassVar[Set[MediaType]]` — the candidate's `signals.medium` must be
  in the set, OR the set is empty (universal), OR the signals do not
  declare a medium.
- `modality: ClassVar[Set[PlaybackModality]]` — `signals.modality` must be
  in the set, OR the set is empty (universal), OR the signals do not
  declare a modality. `PlaybackModality` is imported from `mediavocab`
  and carries values `AUDIO`, `VIDEO`, `INTERACTIVE`, `TEXT`, `UNKNOWN`.
  Lets a caller constrain resolution to audio-only providers via
  `Signals(modality=PlaybackModality.AUDIO)` without changing `medium`.
- `genre_filter: ClassVar[Set[str]]` — at least one tag in
  `signals.content_genres` must overlap the filter, OR the filter is
  empty (no gate).

All three axes must pass; any failing axis skips the provider.

Anime / manga providers therefore declare e.g.
`media = {EPISODIC_SERIES, MOVIE}` plus `genre_filter = {"anime"}`
rather than a fake `MediaType.ANIME` value. Anime is a *genre* per
mediavocab spec axiom 2, not a media type.

`media_archivist.canonicalize._providers_for(providers, medium,
content_genres)` is the dispatcher. `signals_from_entry()` leaves
`modality` unset; that field defaults to `None` and does not gate any
provider unless the caller constructs a `Signals` object directly.

## Configuration

Most providers self-disable via `is_available()` if their optional
upstream dependency or required API key is missing — the registry
stays consistent across environments. Set keys via env vars
documented in each provider's docstring (`DISCOGS_TOKEN`,
`SONARR_URL` + `SONARR_API_KEY`, etc.). The `skyhook` provider
requires no credentials — it uses the same public Servarr proxies
that Sonarr / Radarr / Lidarr use for their own metadata lookups.
