# corpus_pepys — Agent Handoff

## What this repo is

A standalone, self-contained knowledge graph and chat interface for the complete
diary of Samuel Pepys (1660–1669). Built on [DiaryKG](https://github.com/Flux-Frontiers/diary_kg)
and served through the [KGRAG](https://github.com/Flux-Frontiers/kgrag) query
layer. The whole stack — index, API worker, and Streamlit UI — runs locally from
a single Docker image with the index baked in.

This repo is one of a fleet (`gutenberg_kg`, `doc_kg`, `diary_kg`, `KG_utils`)
that share a worker contract and a serving stack. **`gutenberg_kg` is the
reference implementation**: when something here differs from it, that difference
should be deliberate and commented, not incidental. See "Fleet consistency" below.

---

## Current state

| Component | Status |
|---|---|
| DiaryKG index (`.diarykg/`) | Built locally — 7,282 chunks from `pepys_enriched_full.txt`. Gitignored. |
| Docker image (`corpus-pepys:latest`) | Builds from `python:3.12-slim`; published as `egsuchanek/corpus-pepys` |
| KGRAG worker (`make run`) | RunPod serverless API on `http://localhost:8000` |
| Streamlit chat (`make chat` / `--profile chat`) | Pepys-specific UI on `:8501` |
| Synthesis | oMLX / Ollama / OpenAI, selectable per request; off unless an endpoint is configured |
| Image generation | Two host-side servers: FLUX (`make image-server`, :8090, Apple MLX / CUDA 13) and SDXL-Lightning (`make sdxl-server`, :8091, runs anywhere). `make up` picks one; routed through the worker's `imagine` op |
| Tests | 133, no KGRAG environment required (`tests/conftest.py` stubs the stack) |

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
├── scripts/
│   ├── check_pins.py              # Verifies lock/Dockerfile/compose KG pins agree
│   └── ...                        # Source processing (parse, classify, analyse)
├── docker/
│   ├── Dockerfile                 # Self-contained from python:3.12-slim (see below)
│   ├── handler.py                 # Pepys KGRAG handler — search + stats/models/rewrite/imagine
│   ├── docker-compose.yml         # Worker on :8000, chat profile on :8501
│   ├── chat.py                    # Pepys-specific Streamlit UI
│   ├── image_gen.py               # Local mflux/MLX generation — HOST ONLY, not in the image
│   ├── image_server.py            # FastAPI wrapper around image_gen, runs in .venv-image
│   ├── sdxl_server.py             # Portable diffusers image server, runs in .venv-sdxl
│   ├── requirements-image.txt     # Deps for .venv-image (mflux conflicts with the KG stack)
│   ├── requirements-sdxl.txt      # Deps for .venv-sdxl (diffusers conflicts with both)
│   └── .env.example               # HANDLER_SECRET, synthesis and image endpoints
├── tests/                         # conftest.py stubs runpod/kg_rag/kg_utils/streamlit
├── docs/                          # User guide, API reference, build instructions
├── .dockerignore                  # Keeps build-only .diarykg/ artefacts out of the image
├── Makefile                       # All workflows (see below)
└── .diarykg/                      # Built index — gitignored, rebuilt with make
```

There is no `analysis/` directory, despite what older docs claimed.

---

## Makefile workflows

```bash
make install        # runtime + [build] extra + spaCy model (no dev tools)
make install-dev    # as above plus pytest/ruff/pre-commit/ty
make build-corpus   # re-run DiaryTransformer (only if pepys_enriched_full.txt changes)
make build-index    # full diarykg build from pepys_enriched_full.txt (~3 min)
make reindex        # rebuild SQLite + vectors.sqlite only, skip ingest (~1 min)
make check-pins     # verify lock/Dockerfile/compose KG pins agree
make build          # build the image — bakes .diarykg/ into corpus-pepys:latest
make build-all      # build under every runtime installed (docker + apple)
make run            # worker on localhost:8000
make up             # worker + chat (+ host image server where supported)
make sdxl-fetch     # pre-download the SDXL weights (~7 GB) before first use
make chat           # streamlit run docker/chat.py (worker must already be up)
make query          # smoke-test curl (set QUERY="..." to override)
make stop           # halt containers, keep them
make down           # delete containers
make clean          # rm -rf .diarykg/ + remove the image
```

Every container target accepts `RUNTIME=apple` to drive Apple's native
`container` CLI instead of Docker (`docs/APPLE_CONTAINERS.md`). Docker is the
default and works on every platform — `docs/DOCKER.md`. `make build-all` builds
under both when both are installed, which matters because the two runtimes keep
separate image stores.

---

## Architecture

```
data/pepys_enriched_full.txt
        │
        ▼  make build-index
  diarykg build          DiaryTransformer ingest → .diarykg/corpus/*.md
                         dockg build (--no-similar) → graph.sqlite + vectors.sqlite
        │
        ▼  make build
  docker build           FROM python:3.12-slim (NOT the kgrag-worker base)
                         CPU-only torch, then pinned kg-rag/kgmodule-utils/doc-kg/diary-kg
                         COPYs .diarykg/ into /workspace/pepys/
                         pre-downloads BAAI/bge-small-en-v1.5
                         sets HF_HUB_OFFLINE=1 (no runtime HF calls)
        │
        ▼  make run
  handler.py             registers the pepys DiaryKG at startup
                         opens vectors.sqlite via kg_utils.SqliteVecBackend
                         exposes the RunPod serverless API on :8000
        │
        ▼  make chat
  chat.py (Streamlit)    connects to KGRAG_ENDPOINT (default localhost:8000)
                         queries with corpus="diary"
                         synthesis backend chosen in the sidebar
```

---

## Known design decisions

**Not built on `egsuchanek/kgrag-worker`.** The image used to extend that base
and no longer does. The base is shared across the fleet and corpus-agnostic: it
bulk-installs `wheels/*.whl`, so this image inherited gutenberg-kg/metabo-kg
packages it never used plus an unpinned `kg-rag` that shipped at 0.7.0 —
pre-dating `KGEntry.vectors_path` and crashing the worker at registry bootstrap.
It also pulled LanceDB, which nothing here reads. Everything now comes from
explicit PyPI pins in `docker/Dockerfile`.

**The corpus scope is `diary`, not `pepys`.** `handler.py` accepts `diary` and
`all` and rejects anything else. Older docs said `pepys`; every such call failed.

**Retrieval is semantic-first.** `_semantic_search` ranks every chunk by its own
cosine distance with no graph-hop expansion, so the best-matching passages
surface rather than inheriting a flat seed score from expanded neighbours. Clean
passage text and timestamps are hydrated from SQLite afterwards, because the
vector store's `text` column holds the structured embed-text, not the passage.

**SIMILAR_TO edges disabled.** `diarykg reindex` passes `--no-similar`. For a
single-author corpus, all-pairs cosine similarity produces ~5M low-signal edges
(same-author vocabulary uniformity inflates scores). This is why the README and
`docs/BUILDING.md` quote different edge counts — one is a full build, one is a
reindex. Read live numbers from the `stats` op rather than either table.

**Index baked into the image.** `.diarykg/` is copied in at build time so the
container is self-contained — no volumes at runtime. `.dockerignore` keeps the
build-only parts out: `.diarykg/corpus/` (needed by `make reindex`, never read
at serve time), snapshots, embedding caches, WAL sidecars, and any stale
`lancedb/` directory from a pre-sqlite-vec build.

**HuggingFace offline.** `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` are set
in the image; `BAAI/bge-small-en-v1.5` (384-d) is pre-downloaded during build.
The container never needs network access at runtime.

**Image generation is split.** `image_gen.py` needs native Apple MLX via mflux,
which cannot run in a Linux container, and mflux's transformers pin conflicts
with the doc-kg stack. So it runs on the host in an isolated `.venv-image`
(`make image-server`), and the container reaches it over HTTP at
`IMAGE_ENDPOINT`. `image_gen.py` is deliberately **not** copied into the image.

**Both pin sites move together.** `pyproject.toml`'s floor and the Dockerfile
ARG are not belt-and-braces here. This project is `package-mode = false` with no
`pip install .` step, so nothing re-resolves the ARG against the floor — the ARG
is the last word on what the served image installs. Raising only the floor would
leave the container a version behind the index builder. (`gutenberg_kg` is the
opposite: its `pip install .` silently upgrades past a low ARG, which is why its
`check_pins.py` carries a floor check and this one does not — here an ARG below
the floor already fails the exact lock-vs-ARG comparison.)

**KG pins move as a set.** `kg-rag`, `kgmodule-utils`, `doc-kg` and `diary-kg`
are cross-pinned and `==`-pinned in the Dockerfile, so a stale one is a hard
build failure rather than a silent upgrade. `make check-pins` verifies the lock
and the Dockerfile agree — the index is built by the former and read by the
latter, and a mismatch fails silently as empty results. Bump them together and
re-run `poetry lock`.

---

## Fleet consistency

`gutenberg_kg` is the reference. Differences that are **deliberate**:

- The Apple vmnet gateway fallback is `192.168.64.1` here. `gutenberg_kg` says
  `192.168.65.1`, which is Docker Desktop's subnet and is wrong — verified
  against `container network list` on CLI 1.1.0. Do not copy it back.
- No `corpus` selector in the chat sidebar: there is one corpus.
- Loose scripts under `docker/` rather than a packaged `serve/` module, because
  this project is `package-mode = false`.
- `scripts/check_pins.py` exists here and not in `gutenberg_kg`.

Differences that were **accidental** and have been closed (see the CHANGELOG's
unreleased section): the missing `.dockerignore`, a duplicate build-arg in
compose, the unfiltered synthesis-model dropdown, hardcoded sidebar counts, the
`streamlit>=1.35` floor, container detection, and `HANDLER_SECRET` not reaching
the chat service.

Defects this audit found in `gutenberg_kg` itself — a `HANDLER_SECRET` gap in
its chat service, and a Dockerfile pinning `kgmodule-utils` below its own
declared floor — are written up in that repo's
`HANDOFF-corpus-pepys-audit.md`. Both are now fixed there.

---

## Dependencies

- `diary-kg` / `doc-kg` — index build and query API (pull torch + sentence-transformers)
- `kgmodule-utils[synthesis,sqlite-vec]` — synthesis backends, `WorkerClient`,
  `handle_aux_ops`, `SqliteVecBackend`
- `kg-rag` — `KGRegistry` / `KGEntry` and the embedder wrapper
- `BAAI/bge-small-en-v1.5` — embedding model (384-d, baked into the image)
- `streamlit` / `httpx` / `watchdog` / `pillow` — chat UI, installed **in** the image
- oMLX, Ollama, or OpenAI — synthesis backend (optional; oMLX and Ollama run on the host)
- `mflux` — image generation, host-only in `.venv-image`

---

## What's next

- Consider adding a `diarykg mcp` service to the compose stack so the corpus is
  queryable from Claude Code over MCP.
- Richer synthesis prompts — the RAG system prompt is deliberately terse; longer
  passage context may improve answer quality at some latency cost.
