# media_archivist

media_archivist adds remote media as **streams** to your library. It follows
YouTube channels and playlists and indexes YouTube Music, Internet Archive,
Bandcamp and SoundCloud, resolves a fresh playable URL for any entry at play
time, and writes `.strm` files for Jellyfin and Kodi when you ask. It downloads
a file only on request. Apps use it through a JSON API; people use it through a
`media-archivist` CLI and a build-free Web UI.

| Backend | Library |
| --- | --- |
| YouTube, YouTube Music | [`tutubo`](https://github.com/LeMetadatarr/tutubo) |
| Internet Archive | `internetarchive` |
| Bandcamp | [`py_bandcamp`](https://github.com/LeMetadatarr/py_bandcamp) |
| SoundCloud | [`nuvem_de_som`](https://github.com/LeMetadatarr/nuvem_de_som) |

Further providers install as Python packages; see
[provider plugins](docs/plugins.md).

## Install and run

```bash
git clone https://github.com/LeMetadatarr/media-archivist
cd media-archivist && pip install ".[all]"
media-archivist add --db-file talks.json https://www.youtube.com/@SomeChannel
media-archivist serve --db-file talks.json   # http://localhost:8000/
```

Or with Docker: `docker compose -f deploy/docker-compose.yml up -d`. The
service is single-tenant; run it on your LAN or behind a reverse proxy and set
`MEDIA_ARCHIVIST_API_KEY` for anything outside it.

## Documentation

Start at the [documentation index](docs/index.md).

- [Getting started](docs/getting-started.md) and the [Web UI](docs/webui.md)
- [App API](docs/app-api.md): subscriptions, queries, signed links, exports, downloads
- [Running as a service](docs/deploy.md) and [Jellyfin and Kodi](docs/jellyfin.md)
- [CLI](docs/cli.md), [notifications](docs/notifications.md), [datasets](docs/datasets.md)
- [Provider plugins](docs/plugins.md) and [metadata providers](docs/metadatarr.md)

## Related projects

- [`metadatarr`](https://github.com/LeMetadatarr/metadatarr), the cross-source
  metadata resolver used by `canonicalize`
- [`tutubo`](https://github.com/LeMetadatarr/tutubo), the YouTube client

## License

Apache-2.0
