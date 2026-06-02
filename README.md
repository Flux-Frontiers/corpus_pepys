# corpus_pepys

**The Diary of Samuel Pepys — Interactive Semantic Knowledge Graph**

A standalone, queryable knowledge graph of Samuel Pepys' complete diary (1660–1669),
built with [DiaryKG](https://github.com/Flux-Frontiers/diary_kg) and served via the
[KGRAG](https://github.com/Flux-Frontiers/kgrag) federated query layer.

Query 9 years of 17th-century London history — the Great Plague, the Great Fire,
the Restoration court — with natural language, locally, in under a second.

```bash
make build-index   # build the KG from the included corpus (~4 min on a Mac Mini)
make build-image   # bake it into a self-contained Docker image
make run           # start the API on http://localhost:8000
make query QUERY="Great Fire of London"
```

---

## What's in this repo

| Path | Contents |
|---|---|
| `data/pepys_clean.txt` | 3,355 parsed diary entries, one per day (1660-01-01 → 1669-08-02) |
| `data/pepys_enriched_full.txt` | 7,282 semantically enriched, topic-classified chunks |
| `scripts/` | Processing scripts: parsing, NLP transformation, entity analysis |
| `config/` | Topic classification YAML — 30+ Pepys-specific categories |
| `docker/` | Dockerfile, docker-compose, and Pepys-specific KGRAG handler |
| `docs/` | Technical articles describing the pipeline and architecture |
| `analysis/` | Run summaries with corpus statistics |

---

## The corpus

Samuel Pepys kept a diary from January 1660 to May 1669 — nearly a decade of daily
entries covering the most dramatic years of Restoration England.

| Metric | Value |
|---|---|
| Total entries | 3,355 |
| Enriched chunks | 7,282 |
| Time span | 1660-01-01 → 1669-08-02 |
| Embedding shape | 7,282 × 768 (all-mpnet-base-v2) |
| NLP pipeline runtime | ~4 min (Mac Mini, 4 workers) |
| Embedding runtime | ~33 s (4 workers, batch 32) |

Topics span naval administration, court politics, music, science (Royal Society),
domestic life, the Great Plague of 1665, and the Great Fire of London of 1666.

---

## Quick start

### Prerequisites

- Python 3.12+
- [Poetry](https://python-poetry.org/)
- Docker (for the containerised service)
- [diary-kg](https://github.com/Flux-Frontiers/diary_kg) installed:
  ```bash
  pip install diary-kg
  ```

### 1. Build the DiaryKG index

```bash
make build-index
```

This runs `diarykg build` over `data/pepys_enriched_full.txt` and writes
the SQLite + LanceDB index to `.diarykg/` (~2 min, mostly LanceDB vector ingestion).

To rebuild from the raw parsed text instead:

```bash
make build-index CORPUS_SOURCE=data/pepys_clean.txt
```

### 2. Build the Docker image

```bash
make build-image
```

Packages the `.diarykg/` index and the Pepys KGRAG handler into a self-contained
image. No volumes needed at runtime — the index is baked in.

### 3. Run the service

```bash
make run
```

Starts the KGRAG worker on `http://localhost:8000`.

### 4. Query it

```bash
make query QUERY="Great Fire of London"
```

Or with curl:

```bash
curl -s -X POST http://localhost:8000/runsync \
  -H "Content-Type: application/json" \
  -d '{"input":{"query":"Great Fire of London","corpus":"pepys","k":5}}' | jq .
```

---

## API reference

The worker exposes the RunPod serverless API on port 8000.

**Endpoint:** `POST /runsync`

```json
{
  "input": {
    "query":          "string  — required",
    "corpus":         "pepys | all  (default: all)",
    "k":              8,
    "min_score":      0.0,
    "semantic_floor": 0.0,
    "synthesize":     false
  }
}
```

Set `"synthesize": true` with a local Ollama instance (configured in `docker/.env`)
to get a generated answer grounded in the retrieved passages.

---

## Optional: LLM synthesis via Ollama

```bash
cp docker/.env.example docker/.env
# edit VLLM_ENDPOINT_URL and VLLM_MODEL
```

Then query with `"synthesize": true`:

```bash
curl -s -X POST http://localhost:8000/runsync \
  -H "Content-Type: application/json" \
  -d '{"input":{"query":"What did Pepys think of the navy?","synthesize":true,"k":6}}' | jq .
```

---

## Rebuilding the enriched corpus from scratch

The enriched corpus (`data/pepys_enriched_full.txt`) is included in the repo.
To regenerate it from the parsed source:

```bash
make build-corpus    # runs the 5-phase NLP DiaryTransformer pipeline (~4 min)
```

Requires `diary-kg` and its NLP dependencies:

```bash
pip install diary-kg
python -m spacy download en_core_web_sm
```

---

## Repository layout

```
corpus_pepys/
├── data/
│   ├── pepys_clean.txt            # 3,355 parsed entries (timestamped)
│   ├── pepys_clean_small.txt      # 100-entry sample for quick testing
│   └── pepys_enriched_full.txt    # 7,282 enriched chunks (NLP output)
├── scripts/
│   ├── pepys_proper_parse.py      # raw Gutenberg text → pepys_clean.txt
│   ├── diary_transformer_example.py
│   ├── analyze_pepys_entities.py
│   ├── analyze_sentence_structure.py
│   ├── topic_classifier.py
│   └── hindsight_analysis.py
├── config/
│   ├── pepys_only_topics.yaml     # 30+ Pepys topic categories
│   └── topics.yaml                # General diary topic config
├── docker/
│   ├── Dockerfile                 # Self-contained Pepys image
│   ├── docker-compose.yml         # Local dev service
│   ├── handler.py                 # Pepys-specific KGRAG handler
│   └── .env.example
├── docs/
│   ├── pipeline_article.md        # Full pipeline methodology
│   ├── COMPLETE_TECHNICAL_ARTICLE.md
│   ├── PIPELINE_TECHNICAL_DISCLOSURE.md
│   └── nlp_ingestion_workflow.md  # Stage-by-stage pipeline reference
├── analysis/
│   ├── run_summary_enriched.md    # DiaryTransformer run stats
│   └── run_summary_embeddings.md  # Embedding run stats
├── Makefile
└── .gitignore
```

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
  diarykg build             SQLite graph + LanceDB vector index
        │
        ▼
.diarykg/                  Knowledge graph index
        │
        ▼  make build-image
  Docker image              KGRAG handler + baked-in index
        │
        ▼  make run
  http://localhost:8000     Semantic search API
```

---

## Technical background

See the [docs/](docs/) directory for full technical write-ups:

- **[Pipeline Article](docs/pipeline_article.md)** — end-to-end methodology,
  from raw prose to conversational memory graphs; includes the temporal breakthrough
  that made local execution possible with 4B-parameter models.

- **[Technical Disclosure](docs/PIPELINE_TECHNICAL_DISCLOSURE.md)** — engineering
  details: DiaryKG architecture, offline pre-computation design, embedding geometry,
  MRL retrieval benchmarks.

- **[NLP Ingestion Workflow](docs/nlp_ingestion_workflow.md)** — stage-by-stage
  breakdown of the Pepys-specific processing pipeline.

---

## License

The diary text is in the public domain (Project Gutenberg).
All code and documentation in this repository is licensed under the
[Elastic License 2.0](https://www.elastic.co/licensing/elastic-license).

© 2026 Eric G. Suchanek, PhD — Flux-Frontiers
