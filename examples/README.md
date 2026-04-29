# Examples

Recipes for building reusable YouTube datasets with `media_archivist`.

| Script | What it does |
| --- | --- |
| [`index_documentaries.sh`](./index_documentaries.sh) | CLI-only — index three Free Documentary channels into `documentaries.json` and export CSV/JSONL/URL list. |
| [`index_documentaries.py`](./index_documentaries.py) | Same, as a Python script — useful when you want to inject extra metadata or chain into a training pipeline. |
| [`download_with_ytdlp.sh`](./download_with_ytdlp.sh) | Pipe a filtered URL list straight into `yt-dlp` for on-demand extraction. |
| [`hf_dataset.py`](./hf_dataset.py) | Load a JSONL export into 🤗 `datasets` for ML training. |
| [`scripted_export.py`](./scripted_export.py) | Drive `media_archivist` from Python using the CLI's pydantic validators. |
| [`cross_source_dataset.py`](./cross_source_dataset.py) | Index the same artist on YT-Music + Bandcamp + SoundCloud, fingerprint-link duplicates, dedupe to canonical JSONL. |

All examples use **explicit `--db-file PATH`** so the resulting JSON file lives
next to the script and can be committed alongside the rest of the dataset.
