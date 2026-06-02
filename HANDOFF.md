# corpus_pepys — Agent Handoff

## What this repo is

A standalone, self-contained knowledge graph and chat interface for the complete
diary of Samuel Pepys (1660–1669). Built on [DiaryKG](https://github.com/Flux-Frontiers/diary_kg)
and served via the [KGRAG](https://github.com/Flux-Frontiers/kgrag) federated
query layer. The entire stack — index, API worker, and Streamlit UI — runs
locally from a single Docker image.

---

## Current state

Everything is working end-to-end:

| Component | Status |
|---|---|
| DiaryKG index (`.diarykg/`) | Built — 7,282 chunks from `pepys_enriched_full.txt` |
| Docker image (`corpus-pepys:latest`) | Built and smoke-tested |
| KGRAG worker (`make run`) | Runs on `http://localhost:8000` |
| Streamlit chat (`make chat`) | Pepys-specific UI, working with synthesis |
| Synthesis (Ollama + qwen3:4b) | Working — `think: false`, `<think>` stripping in place |

---

## Key files

```
corpus_pepys/
├── data/
│   ├── pepys_clean.txt            # 3,355 parsed entries (source of truth)
│   ├── pepys_clean_small.txt      # 100-entry sample for quick testing
│   └── pepys_enriched_full.txt    # 7,282 NLP-enriched chunks (index input)
├── config/
│   ├── pepys_only_topics.yaml     # 30+ Pepys topic categories (YAML rules)
│   └── topics.yaml                # General diary topic config
├── scripts/                       # Processing scripts (parse, analyse, etc.)
├── docker/
│   ├── Dockerfile                 # Extends kgrag-worker; installs diary-kg + watchdog
│   ├── handler.py                 # Pepys-specific KGRAG handler (corpus="pepys")
│   ├── docker-compose.yml         # Worker on :8000, chat profile on :8501
│   ├── chat.py                    # Pepys-specific Streamlit UI
│   └── .env.example               # HANDLER_SECRET, VLLM_* vars
├── docs/                          # Technical articles from diary_kg
├── analysis/                      # Run summaries (NLP + embedding stats)
├── Makefile                       # All workflows (see below)
└── .diarykg/                      # Built index — gitignored, rebuilt with make
```

---

## Makefile workflows

```bash
make build-corpus   # re-run DiaryTransformer (only if pepys_enriched_full.txt changes)
make build-index    # full diarykg build from pepys_enriched_full.txt (~3 min)
make reindex        # rebuild SQLite + LanceDB only, skip ingest (~1 min)
make build-image    # docker build — bakes .diarykg/ into corpus-pepys:latest
make run            # docker compose up -d  →  worker on localhost:8000
make stop           # docker compose down
make chat           # streamlit run docker/chat.py  →  UI on localhost:8501
make query          # smoke-test curl (set QUERY="..." to override)
make clean          # rm -rf .diarykg/ + docker image rm
```

---

## Architecture

```
data/pepys_enriched_full.txt
        │
        ▼  make build-index
  diarykg build          DiaryTransformer ingest → .diarykg/corpus/*.md
                         dockg build (--no-similar) → graph.sqlite + lancedb/
        │
        ▼  make build-image
  docker build           COPYs .diarykg/ into /workspace/pepys/
                         pip install diary-kg watchdog
                         pre-downloads BAAI/bge-small-en-v1.5
                         sets HF_HUB_OFFLINE=1 (no runtime HF calls)
        │
        ▼  make run
  handler.py             registers pepys DiaryKG at startup
                         exposes RunPod serverless API on :8000
        │
        ▼  make chat
  chat.py (Streamlit)    connects to localhost:8000
                         corpus hardcoded to "pepys"
                         synthesis via Ollama (qwen3:4b, think=false)
```

---

## Known design decisions

**SIMILAR_TO edges disabled** — `diarykg reindex` and `diarykg build` both pass
`--no-similar` to `dockg build`. For a single-author corpus, all-pairs cosine
similarity produces ~5M low-signal edges (same-author vocabulary uniformity
inflates scores). The LanceDB ANN index and HAS_TOPIC graph already capture
thematic structure.

**Index baked into Docker image** — `.diarykg/` is copied into the image at
build time so the container is fully self-contained. No volumes needed at
runtime. Rebuild with `make build-index && make build-image` after corpus changes.

**HuggingFace offline** — `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` are
set in the image. The embedding model (`BAAI/bge-small-en-v1.5`) is
pre-downloaded during `docker build`. The container never needs network access
at runtime.

**Synthesis** — Ollama on the host is reached from Docker via
`http://host.docker.internal:11434`. Configure in `docker/.env` (copy from
`.env.example`). `think: false` is passed to suppress qwen3 reasoning output;
any leaking `<think>…</think>` blocks are stripped in `handler.py`.

---

## Dependencies

- `diary-kg` (PyPI) — DiaryKG index, query, CLI
- `kgrag-worker` (Docker base image: `egsuchanek/kgrag-worker:latest`) — KGRAG
  orchestrator, registry, embedder
- `BAAI/bge-small-en-v1.5` — embedding model (384-d, baked into image)
- Ollama + `qwen3:4b` — local LLM for synthesis (optional; runs on host)
- `streamlit`, `httpx`, `watchdog` — chat UI (run locally, not in Docker)

---

## What's next

- Push `corpus-pepys:latest` to Docker Hub for public distribution
- Add `git init` + proper remote to this repo (currently one orphan commit)
- Consider adding a `diarykg mcp` service to the compose stack so the corpus
  is queryable by Claude Code via MCP
- Explore richer synthesis prompts — current context uses short summaries;
  passing full chunk text would improve answer quality
- Add suggested queries / example questions to the Streamlit sidebar
