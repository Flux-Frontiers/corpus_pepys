# Building from Source

This document covers rebuilding the corpus index, Docker image, and NLP enrichment
pipeline from scratch. You do not need any of this to run the service — see the
main [README](../README.md) for the Docker Hub fast path.

---

## Prerequisites

- Python 3.12+
- [Poetry](https://python-poetry.org/)
- Docker
- `diary-kg` installed (global or via uv):
  ```bash
  pip install diary-kg
  # or
  uv tool install diary-kg
  ```

---

## Phase 1 — Build the DiaryKG index

```bash
make build-index
```

Runs `diarykg build` over `data/pepys_enriched_full.txt` and writes the
SQLite + sqlite-vec index (`graph.sqlite` + `vectors.sqlite`) to `.diarykg/` (~2 min on a Mac Mini).

To rebuild from the raw parsed text instead:

```bash
make build-index CORPUS_SOURCE=data/pepys_clean.txt
```

To reindex without re-ingesting (faster — skips the ingest pass, rebuilds
SQLite + vectors.sqlite from the existing `.diarykg/corpus/` files):

```bash
make reindex
```

---

## Phase 2 — Build the Docker image

```bash
make build-image
```

Bakes the `.diarykg/` index into a self-contained image. The container needs
no volumes at runtime — the index is embedded at build time.

---

## Phase 3 — Rebuild the enriched corpus from scratch

The enriched corpus (`data/pepys_enriched_full.txt`) is included in the repo
and does not need to be regenerated for normal use. To run the full 5-phase
NLP pipeline from the raw parsed source:

```bash
make build-corpus
```

Requires the NLP extras:

```bash
pip install diary-kg
python -m spacy download en_core_web_sm
```

Runtime: ~4 min on a Mac Mini (4 workers).

---

## Pipeline overview

```
data/pepys_clean.txt       (3,355 timestamped entries)
        │
        ▼  make build-corpus
  DiaryTransformer          5-phase NLP pipeline
   Phase 1: spaCy feature extraction + k-means diversity selection
   Phase 2: sentence-transformers chunking (sentence_group strategy)
   Phase 3: TF-IDF k-means topic discovery (unsupervised)
   Phase 4: TopicClassifier refinement (YAML rules, hybrid scoring)
   Phase 5: EntryChunk creation + structured output
        │
        ▼
data/pepys_enriched_full.txt   (7,282 enriched chunks)
        │
        ▼  make build-index
  diarykg build             SQLite graph + sqlite-vec vector index
        │
        ▼
.diarykg/                  Knowledge graph index
        │
        ▼  make build-image
  Docker image              KGRAG handler + baked-in index
        │
        ▼  docker push
  egsuchanek/corpus-pepys:latest   on Docker Hub
```

---

## Corpus statistics

| Metric | Value |
|---|---|
| Total entries | 3,355 |
| Enriched chunks | 7,282 |
| Time span | 1660-01-01 → 1669-08-02 |
| Embedding shape | 7,282 × 384 (BAAI/bge-small-en-v1.5) |
| NLP pipeline runtime | ~4 min (Mac Mini, 4 workers) |
| Embedding runtime | ~33 s (4 workers, batch 32) |
| KG nodes | 41,738 |
| KG edges | 564,311 |

The node/edge counts above are from a full build with `SIMILAR_TO` edges. `make
reindex` runs `diarykg reindex`, which disables them (see the Makefile), giving
the ~334K edge count quoted in the README. For the live figures of whatever
index is actually being served, ask the worker rather than either table:

```bash
curl -s -X POST http://localhost:8000/runsync \
  -H "Content-Type: application/json" \
  -d '{"input":{"op":"stats"}}' | python3 -m json.tool
```

The chat UI's sidebar reads the same `stats` op, so its counts always describe
the running index.

---

## Scripts

| Script | Purpose |
|---|---|
| `scripts/pepys_proper_parse.py` | Raw Gutenberg text → `pepys_clean.txt` (timestamped entries) |
| `scripts/topic_classifier.py` | YAML-rule topic classifier used by DiaryTransformer Phase 4 |
| `scripts/analyze_sentence_structure.py` | Sentence length and chunk size analysis for pipeline tuning |
