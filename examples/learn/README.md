# Zero-to-hero — `media-archivist` learning path

A five-step progression from "index a row" to "export a typed dataset".
Each script stands alone, uses only public-domain content for live calls,
and writes to a temp dir (no leftover state).

```bash
for f in examples/learn/[0-9]*.py; do
  echo "=== $f ==="
  python "$f"
done
```

| # | File | What you learn |
|---|---|---|
| 1 | `01_index_an_archive.py` | Use an Archivist class to fetch metadata into a JSON DB |
| 2 | `02_query_via_view.py` | Project source-shaped rows to a unified `MediaEntry` |
| 3 | `03_canonicalize.py` | Resolve every row against external sources (`metadatarr.resolve`) |
| 4 | `04_entity_sidecar.py` | Group resolved entities by `EntityRole` and `EntityKind` |
| 5 | `05_export_dataset.py` | Ship the canonical view as a typed dataset |

The other examples in this directory are larger pipelines (`hf_dataset.py`,
`scripted_export.py`, `cross_source_dataset.py`) and per-source recipes
(`anime_books.py`, `canonicalize_movies.py`, `documentaries`). Start with
`learn/` if you're new; the recipes are reference once you've done the tour.
