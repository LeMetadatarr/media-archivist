# Recipe: ML-ready dataset with deterministic splits

Build a HuggingFace `datasets`-compatible JSONL with train/val/test splits that remain stable across re-runs via fingerprint-based hashing.

## Goal

Create an ML dataset suitable for training recommender systems, classifiers, or embeddings by:
1. Indexing media from multiple sources.
2. Deduplicating to a canonical view.
3. Generating deterministic splits (train/val/test) via stable hashing.
4. Exporting to HuggingFace `datasets` format.
5. (Optional) Publishing to HuggingFace Hub.

## Prerequisites

```bash
# Install media_archivist and ML dependencies
pip install media_archivist[all] datasets

# HF Hub token (optional, for uploading to Hub)
huggingface-cli login
```

## Step 1: Build a diverse index

Create a dataset spanning multiple channels/sources to capture diversity:

```bash
$ media-archivist add --db-file recipe_ml_dataset.json \
    https://www.youtube.com/@StatQuest \
    https://www.youtube.com/@3Blue1Brown \
    https://www.youtube.com/@TedEd

$ media-archivist stats --db-file recipe_ml_dataset.json
```

Expected output:
```
Total entries: 267
Sources:
  youtube: 267

Field coverage:
  title: 267/267 (100%)
  description: 245/267 (92%)
  tags: 201/267 (75%)
  duration: 267/267 (100%)
```

Consider indexing multiple sources (YouTube, YouTube Music, Internet Archive) to increase diversity:

```bash
$ media-archivist add --db-file recipe_ml_dataset.json --ia "classic_cartoons"
$ media-archivist add --db-file recipe_ml_dataset.json --music "classical music"
```

## Step 2: Clean and prepare

Remove entries with missing critical fields:

```bash
# Remove entries without descriptions (needed for text-based models)
$ media-archivist prune --db-file recipe_ml_dataset.json --missing description

# Optionally filter by duration (e.g., only videos > 5 min)
$ media-archivist prune --db-file recipe_ml_dataset.json --below 300
```

Verify coverage:

```bash
$ media-archivist stats --db-file recipe_ml_dataset.json
```

## Step 3: Link and dedupe

If you indexed multiple sources, deduplicate:

```bash
$ media-archivist link --db-file recipe_ml_dataset.json
$ media-archivist dedupe --db-file recipe_ml_dataset.json \
    -o recipe_ml_dataset_canonical.jsonl \
    --prefer youtube_music,internet_archive,youtube
```

Expected output:
```
Deduplicated 389 entries into 267 canonical MediaEntry rows
Wrote 267 rows to recipe_ml_dataset_canonical.jsonl
```

If you only indexed one source, skip deduping and export directly:

```bash
$ media-archivist export --db-file recipe_ml_dataset.json \
    --format jsonl \
    -o recipe_ml_dataset_raw.jsonl
```

## Step 4: Create deterministic splits

Use fingerprint-based hashing to split data reproducibly. This ensures that:
- The same entry always lands in the same split (train/val/test).
- Splits remain consistent across re-runs and different machines.
- You can add new data without disturbing existing splits.

Create a Python script:

```python
# create_splits.py
import json
import hashlib
from pathlib import Path

JSONL_FILE = Path("recipe_ml_dataset_canonical.jsonl")
OUTPUT_DIR = Path("recipe_ml_dataset_splits")
OUTPUT_DIR.mkdir(exist_ok=True)

# Split ratios
TRAIN_RATIO = 0.7  # 70% training
VAL_RATIO = 0.15   # 15% validation
TEST_RATIO = 0.15  # 15% test

def fingerprint_stable_split(entry_id: str, train_r=TRAIN_RATIO, 
                               val_r=VAL_RATIO) -> str:
    """Assign entry to train/val/test based on stable hash of its ID."""
    # Use SHA1 of the entry ID for deterministic split
    h = int(hashlib.sha1(entry_id.encode()).hexdigest(), 16)
    bucket = h % 100  # 0-99
    
    if bucket < train_r * 100:
        return "train"
    elif bucket < (train_r + val_r) * 100:
        return "val"
    else:
        return "test"

splits = {"train": [], "val": [], "test": []}
split_counts = {"train": 0, "val": 0, "test": 0}

with open(JSONL_FILE) as f:
    for line in f:
        entry = json.loads(line)
        entry_id = entry.get("id")
        if not entry_id:
            print(f"Warning: entry missing 'id': {entry}")
            continue
        
        split_name = fingerprint_stable_split(entry_id)
        split_counts[split_name] += 1
        
        # Write to split-specific JSONL
        splits[split_name].append(entry)

# Write split files
for split_name, entries in splits.items():
    out_file = OUTPUT_DIR / f"{split_name}.jsonl"
    with open(out_file, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")
    print(f"Wrote {len(entries)} entries to {out_file}")

# Write split manifest
manifest = {
    "total": sum(split_counts.values()),
    "splits": split_counts,
    "ratios": {
        "train": split_counts["train"] / sum(split_counts.values()),
        "val": split_counts["val"] / sum(split_counts.values()),
        "test": split_counts["test"] / sum(split_counts.values()),
    },
}

manifest_file = OUTPUT_DIR / "manifest.json"
with open(manifest_file, "w") as f:
    json.dump(manifest, f, indent=2)
print(f"\nManifest: {manifest_file}")
print(json.dumps(manifest, indent=2))
```

Run:

```bash
$ python create_splits.py
```

Expected output:
```
Wrote 187 entries to recipe_ml_dataset_splits/train.jsonl
Wrote 40 entries to recipe_ml_dataset_splits/val.jsonl
Wrote 40 entries to recipe_ml_dataset_splits/test.jsonl

Manifest: recipe_ml_dataset_splits/manifest.json
{
  "total": 267,
  "splits": {
    "train": 187,
    "val": 40,
    "test": 40
  },
  "ratios": {
    "train": 0.7,
    "val": 0.15,
    "test": 0.15
  }
}
```

## Step 5: Load into Hugging Face datasets

```python
# load_dataset.py
from datasets import load_dataset
from pathlib import Path

splits_dir = Path("recipe_ml_dataset_splits")

# Load each split
train = load_dataset("json", data_files=str(splits_dir / "train.jsonl"))["train"]
val = load_dataset("json", data_files=str(splits_dir / "val.jsonl"))["val"]
test = load_dataset("json", data_files=str(splits_dir / "test.jsonl"))["test"]

print(f"Train: {len(train)} entries")
print(f"Val: {len(val)} entries")
print(f"Test: {len(test)} entries")

# Inspect schema
print("\nTrain sample:")
print(train[0])

# Example: Extract just title and description for text classification
train_text = train.map(lambda x: {
    "id": x["id"],
    "title": x["title"],
    "description": x.get("description", ""),
    "source": x["source"],
}, remove_columns=[c for c in train.column_names if c not in ["id", "title", "description", "source"]])

print(f"\nProjected train schema: {train_text.column_names}")
print(f"First entry: {train_text[0]}")
```

Run:

```bash
$ python load_dataset.py
```

Expected output:
```
Train: 187 entries
Val: 40 entries
Test: 40 entries

Train sample:
{
  'id': 'a1b2c3d4e5f6...',
  'source': 'youtube',
  'url': 'https://www.youtube.com/watch?v=...',
  'title': 'The Essence of Algebra',
  'artist': None,
  'album': None,
  'duration': 1234,
  'year': 2023,
  'tags': ['mathematics', 'education'],
  ...
}

Projected train schema: ['id', 'title', 'description', 'source']
First entry: {
  'id': 'a1b2c3d4e5f6...',
  'title': 'The Essence of Algebra',
  'description': 'Explore the foundational concepts...',
  'source': 'youtube'
}
```

## Step 6: Use for ML training

Example: Train a text classifier to predict `source` from title + description.

```python
# train_classifier.py
from datasets import DatasetDict, load_dataset
from transformers import AutoTokenizer, AutoModelForSequenceClassification, Trainer, TrainingArguments
from pathlib import Path

splits_dir = Path("recipe_ml_dataset_splits")

# Load splits
train_ds = load_dataset("json", data_files=str(splits_dir / "train.jsonl"))["train"]
val_ds = load_dataset("json", data_files=str(splits_dir / "val.jsonl"))["val"]
test_ds = load_dataset("json", data_files=str(splits_dir / "test.jsonl"))["test"]

dataset = DatasetDict({
    "train": train_ds,
    "validation": val_ds,
    "test": test_ds,
})

# Map source names to IDs
label2id = {src: i for i, src in enumerate(sorted(set(dataset["train"]["source"])))}
id2label = {v: k for k, v in label2id.items()}

# Tokenize
tokenizer = AutoTokenizer.from_pretrained("distilbert-base-uncased")

def preprocess(examples):
    texts = [f"Title: {t}\nDescription: {d}" for t, d in 
             zip(examples["title"], examples.get("description", [""] * len(examples["title"])))]
    encoded = tokenizer(texts, truncation=True, padding="max_length", max_length=512)
    encoded["label"] = [label2id[src] for src in examples["source"]]
    return encoded

dataset = dataset.map(preprocess, batched=True)
dataset = dataset.remove_columns(["title", "description", "source", "id", "url", ...])

# Train classifier
model = AutoModelForSequenceClassification.from_pretrained(
    "distilbert-base-uncased",
    num_labels=len(label2id),
    id2label=id2label,
    label2id=label2id,
)

training_args = TrainingArguments(
    output_dir="recipe_ml_dataset_classifier",
    per_device_train_batch_size=16,
    per_device_eval_batch_size=16,
    num_train_epochs=3,
    evaluation_strategy="epoch",
    logging_steps=100,
)

trainer = Trainer(
    model=model,
    args=training_args,
    train_dataset=dataset["train"],
    eval_dataset=dataset["validation"],
)

trainer.train()
print("Training complete!")

# Evaluate on test set
results = trainer.evaluate(dataset["test"])
print(f"Test accuracy: {results['eval_accuracy']:.4f}")
```

## Step 7: Publish to HF Hub

To publish your dataset directly to HuggingFace Hub:

```bash
media-archivist export --db-file recipe_ml_dataset.json \
    --format huggingface \
    --hf-repo your-username/media-archive-dataset \
    --hf-splits train.jsonl,val.jsonl,test.jsonl
```

For now, upload manually:

```python
from datasets import DatasetDict

dataset.push_to_hub("your-username/free-educational-videos")
```

Then others can load it:

```python
from datasets import load_dataset

ds = load_dataset("your-username/free-educational-videos")
```

## What to do next

- **Add data cards:** Document your dataset with bias, licensing, and intended use.
  ```bash
  cat > recipe_ml_dataset_DATACARD.md <<'EOF'
  # Free Educational Videos Dataset
  
  ## Source
  Indexed from YouTube (StatQuest, 3Blue1Brown, TED-Ed) via media_archivist.
  
  ## Schema
  - title (str)
  - description (str)
  - duration (int, seconds)
  - source (str, one of "youtube", "internet_archive", ...)
  - tags (list of str)
  - url (str)
  
  ## Splits
  - train: 187 entries
  - validation: 40 entries
  - test: 40 entries
  
  ## Licensing
  YouTube videos are subject to their respective creators' terms. Use responsibly.
  EOF
  ```

- **Fine-tune embeddings:** Use the canonical JSONL to train or fine-tune embeddings for content-based recommendations.

- **Build a recommender:** Use titles + descriptions to train a collaborative filtering model.

- **Version control:** Commit splits and manifest to Git so collaborators use the same train/val/test boundaries.

## See also

- [Cross-source music library](./music-library-from-bandcamp-soundcloud-and-ytmusic.md) — for music-specific deduping.
- [Canonical view & dedupe](../canonical.md) — fingerprinting details.
- [HuggingFace datasets](https://huggingface.co/docs/datasets/) — full API reference.
