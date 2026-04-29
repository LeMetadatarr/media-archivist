"""v0.6 surfaces: enriched models, dataset card, deterministic splits, snapshot/diff."""
from __future__ import annotations

import json

from media_archivist.hub import build_card, split_jsonl
from media_archivist.models.dataset_card import DEFAULT_LICENSES, DatasetCard
from media_archivist.models.enriched import (
    ContentTypeBlock,
    EnrichedBlock,
    LyricsBlock,
    TranscriptBlock,
    TranscriptCue,
)
from media_archivist.snapshot import diff, snapshot
from media_archivist.storage import EnvelopeJsonStorage


# ---------------------------------------------------------------------------
# Enriched models
# ---------------------------------------------------------------------------

def test_enriched_block_round_trip():
    block = EnrichedBlock(
        lyrics=LyricsBlock(text="la la la", source="bandcamp"),
        content_type=ContentTypeBlock(label="documentary"),
        transcript=TranscriptBlock(language="en", cues=[
            TranscriptCue(start=0.0, end=2.5, text="hello"),
        ]),
    )
    again = EnrichedBlock.model_validate(block.model_dump(mode="json"))
    assert again.lyrics.text == "la la la"
    assert again.content_type.label == "documentary"
    assert again.transcript.cues[0].text == "hello"


# ---------------------------------------------------------------------------
# Dataset card
# ---------------------------------------------------------------------------

def test_dataset_card_render_includes_source_mix():
    card = DatasetCard(
        name="my-dataset",
        description="Test.",
        license="cc0-1.0",
        total_entries=10,
        source_mix={"bandcamp": 7, "youtube_music": 3},
        canonical_records=8,
        quarantined=0,
        licenses_by_source={"bandcamp": DEFAULT_LICENSES["bandcamp"]},
    )
    md = card.to_markdown()
    assert md.startswith("---\n")
    assert "license: cc0-1.0" in md
    assert "Total entries: 10" in md
    assert "| `bandcamp` | 7 |" in md
    assert "Provenance" in md


def test_build_card_uses_envelope_meta(tmp_path):
    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db["https://x.bandcamp.com/track/y"] = {
        "source": "bandcamp", "url": "https://x.bandcamp.com/track/y",
        "title": "t",
    }
    db.store()
    card = build_card(str(db_path), name="x", description="y")
    assert card.source_mix == {"bandcamp": 1}
    assert card.licenses_by_source.get("bandcamp")


# ---------------------------------------------------------------------------
# Deterministic splits
# ---------------------------------------------------------------------------

def test_split_jsonl_is_deterministic_and_sums_to_total():
    rows = [{"id": str(i), "title": f"t{i}"} for i in range(500)]
    a = split_jsonl(rows, "train:0.8,val:0.1,test:0.1")
    b = split_jsonl(rows, "train:0.8,val:0.1,test:0.1")
    assert {k: [r["id"] for r in v] for k, v in a.items()} == \
           {k: [r["id"] for r in v] for k, v in b.items()}
    assert sum(len(v) for v in a.values()) == 500


def test_split_jsonl_keys_off_canonical_id_when_present():
    """Same canonical_id → always lands in the same split, regardless of url."""
    rows = [
        {"id": "row1", "url": "u1", "canonical_id": "shared"},
        {"id": "row2", "url": "u2", "canonical_id": "shared"},
    ]
    splits = split_jsonl(rows, "a:0.5,b:0.5")
    placement = {row["id"]: split for split, items in splits.items() for row in items}
    assert placement["row1"] == placement["row2"]


# ---------------------------------------------------------------------------
# Snapshot / diff
# ---------------------------------------------------------------------------

def test_snapshot_creates_dated_copy(tmp_path):
    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db["a"] = {"source": "youtube", "url": "a", "videoId": "a", "title": "t"}
    db.store()
    out = snapshot(str(db_path), label="pre-prune")
    assert out.exists()
    assert out.parent.name == ".snapshots"
    assert out.stem.endswith("-pre-prune")


def test_diff_classifies_added_removed_changed(tmp_path):
    a_path = tmp_path / "a.json"
    b_path = tmp_path / "b.json"
    a = EnvelopeJsonStorage(str(a_path))
    a["u1"] = {"source": "youtube", "url": "u1", "videoId": "u1", "title": "t1"}
    a["u2"] = {"source": "youtube", "url": "u2", "videoId": "u2", "title": "t2"}
    a.store()
    b = EnvelopeJsonStorage(str(b_path))
    b["u2"] = {"source": "youtube", "url": "u2", "videoId": "u2", "title": "t2-new"}
    b["u3"] = {"source": "youtube", "url": "u3", "videoId": "u3", "title": "t3"}
    b.store()
    result = diff(str(a_path), str(b_path))
    assert result["added"] == ["u3"]
    assert result["removed"] == ["u1"]
    assert result["changed"] == ["u2"]


# ---------------------------------------------------------------------------
# Enrich orchestrator basic shape
# ---------------------------------------------------------------------------

def test_enrich_skips_when_no_kinds_apply(tmp_path):
    """Bandcamp-only DB with content_type request should be a no-op (YT-only enricher)."""
    from media_archivist.enrich import EnrichKind, enrich

    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db["https://x.bandcamp.com/track/y"] = {
        "source": "bandcamp",
        "url": "https://x.bandcamp.com/track/y",
        "title": "t",
    }
    db.store()
    processed, modified = enrich(str(db_path), [EnrichKind.CONTENT_TYPE])
    assert processed == 1
    assert modified == 0
