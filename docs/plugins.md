# Provider plugins

A provider turns a URL into index entries: YouTube, YouTube Music, the
Internet Archive, Bandcamp and SoundCloud are providers. Further providers
are Python packages that register a class under the entry-point group
`media_archivist.providers`; installing the package makes the server aware of
it. The five built-in providers register through the same mechanism and carry
the same checks.

## Writing a provider

Register the class in the package's `pyproject.toml`. The entry-point name is
the provider's name: lowercase letters, digits, `-` and `_`.

```toml
[project.entry-points."media_archivist.providers"]
example = "example_provider:ExampleProvider"
```

The class subclasses `media_archivist.base.JsonArchivist`, or offers the same
surface (`media_archivist.plugins.ProviderProtocol`):

| Member | Meaning |
| --- | --- |
| `API_VERSION` | An integer. The server loads the class only when it equals `media_archivist.plugins.API_VERSION`, which is `1`. `JsonArchivist` sets it. |
| `__init__(db_path=, required_kwords=, blacklisted_kwords=, min_duration=)` | Opens the JSON database at `db_path`. |
| `archive(url)` | Fetches `url` and stores its entries in the database. |
| `db` | The JSON database. A provider exposes `db` as a plain attribute, writes every row through `self.db` and keeps no other handle to it: a row written through a kept handle is recorded as archived by the provider only when it is new or changed, and the server logs a warning when it sees such a handle. |
| `video_urls` | The keys already stored; a sync compares it before and after `archive`. |
| `url_hosts` | Hosts that identify the provider's URLs, such as `("example.test",)`. A subscription to a URL containing one of them uses this provider when no backend is given. Longest host wins. |
| `sources` | The `source` values of the entries the provider stores. Each is claimed by one provider; a second claim is a load error. |
| `adult` | `True` for a provider of adult content. Default `False`. |

The index and the canonical view read entries whose `source` is one of the
five built-in values. A provider's entries reach the index only when they carry
one of those values; `sources` is what the export guard and the
`sources` clash check use. Providers of adult content are shipped in their own
packages, not in this repository.

## Installed is not enabled

Installing a package adds the provider to the list at
`GET /api/v1/providers`, nothing more. A provider works only when its name is
in the `enabled_providers` setting, which starts as the five built-in names:

```bash
curl -X PATCH "$ARCHIVIST/api/v1/settings" -H "X-Api-Key: $KEY" \
     -H 'Content-Type: application/json' \
     -d '{"enabled_providers": ["youtube", "ia", "music", "bandcamp", "soundcloud", "example"]}'
```

Names in `enabled_providers` and `adult_export_providers` must belong to an
installed provider, loaded or not; anything else is rejected with HTTP 400.
An empty `enabled_providers` enables nothing. A name stays in the setting if
its package is later uninstalled, and the provider is then reported as
unavailable.

Adding a subscription, running a sync or queueing an archive task for a
provider that is installed but not enabled fails with a message saying so; a
scheduled sync records it as that subscription's error.

## Failures

The entry points are read once, when the first request needs them. A plugin
that cannot be imported, exits while importing, has the wrong `API_VERSION`,
lacks `archive`, declares malformed attributes, has a name that is not
lowercase letters, digits, `-` and `_`, reuses a name or claims a source
another provider owns is skipped. The reason is logged and listed as `error`
on its row at `/api/v1/providers`; a name that clashes with a built-in is
listed once, as `<name> [<entry point>]`. The other providers and the server
are unaffected.

Loading imports the plugin's module inside the server process. A module that
never returns from its import blocks the first request that needs the
providers, and the server does not guard against that. A plugin that raises
while syncing fails that one sync, as any provider error does.

## Adult providers

A provider with `adult = True` is refused for `.strm` export and Jellyfin
refresh unless its name is in the `adult_export_providers` setting, empty by
default. The switch is per provider: enabling one adult provider leaves the
others refused.

An export is refused, before any file or folder is written, when an unswitched
adult provider is named by anything the guard reads:

* the provider that claims the `source` of an entry;
* the backends in the entry row's `archived_by` field, which the server sets
  on every row an archive call writes, whether it came from the archive task,
  a subscription sync or `media-archivist add`;
* the backend of every subscription the entry belongs to;
* the backend of the subscription named by a subscription or playlist export.

`archived_by` only grows: archiving an entry again, or through another
backend, adds that backend and never removes an adult one, whether or not the
content changed, and rows are stamped even when the archivist raises after
writing them. Two cases are not covered by this field and rely on the other
three sources: rows stored before the field existed, and rows a plugin writes
from a thread after `archive()` has returned (or through a reference to the
database it kept from construction, when the row is rewritten unchanged).
The guard covers `.strm` export, Jellyfin refresh and collections
exported as `.strm` files.

It fails closed: a subscription whose backend is not loaded, because the
package was uninstalled or failed to load, is refused too, since its flag is
unknown. A built-in provider whose client library is missing is not adult and
does not block exports. The refusal covers `POST /api/v1/exports`
(HTTP 400), the automatic export of a subscription (logged, nothing written) and
`media-archivist strm-export` (exit with an error).

## Checking a plugin

`GET /api/v1/providers` returns `api_version` and one row per provider with its
name, whether it is built in, its `api_version`, `adult`, `enabled`,
`adult_export` and `error`. A provider that failed to load has `error` set and
no `api_version`.
