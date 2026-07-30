# Roadmap

Planned work that is not yet part of the shipped feature set.

## Resolver providers

- Dedicated `tvdb`, `openlibrary`, and `imdb` providers. TVDB- and
  OpenLibrary-shaped data is currently reached through `skyhook` and
  Wikidata joins. Dedicated providers would expose their native ids
  and lookups directly.

## Testing

- HTTP fixture-based integration tests for the live providers
  (cassette / record-replay pattern), complementing the offline
  stub-provider suites.

The current feature set, cross-source backends, the two-tier schema and
canonical view, fingerprint dedup, disambiguation and external-id
resolution, entities and relations, release variants, dataset enrichment
and publishing, and the FastAPI service, is documented across the rest
of this site. Start at the [documentation index](./index.md).

---
[← CI / Release Automation](ci.md) · [Home](index.md)
