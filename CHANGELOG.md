# Changelog

All notable changes to corpus_pepys are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

### Added
- **Test suite** (`tests/`) — 58 unit tests covering `docker/handler.py` and
  `docker/image_gen.py`, runnable with no KGRAG environment:
  - `tests/conftest.py`: stubs `runpod`, `kg_rag`, `kg_utils`, and `lancedb`
    into `sys.modules` before import so handler.py's startup code runs against
    lightweight mocks
  - `tests/test_handler.py` (36 tests): `_rows_to_hits` (score/filter/defaults),
    `_attach_diary_fields` (temp-SQLite hydration), `_semantic_search` (no-table
    guard, semantic-floor), and `handler()` dispatch (auth, query validation,
    corpus validation, response shape, synthesis on/off)
  - `tests/test_image_gen.py` (22 tests): `_ASPECT_SIZES` completeness and
    orientation, `_load_model` cache hit path, and `generate()` (aspect ratio
    lookup + fallback, seed, output path, steps override)
- `Makefile`: `make test` (runs pytest) and `make lint` (ruff check + format
  check on `docker/` and `scripts/`) targets
- `scripts/check_standard_queries.py` — validation harness that runs the eight
  standard diary queries against a live worker and asserts each returns at least
  one hit, printing the top results with scores and timestamps
- `docker/requirements-image.txt` + isolated `.venv-image` for the host-side FLUX
  image server — `make image-server` now creates/installs into `.venv-image`
  instead of relying on the Poetry env (mflux is not a project dependency, so the
  previous `poetry run python docker/image_server.py` failed at import)
- `docker/image_server.py`: `IMAGE_PRELOAD` env gate (default off) — the model
  is lazy-loaded on first generation request, so endpoint-only deployments don't
  need mflux model imports at startup

- **`make install` / `make install-dev` / `make install-model`** — one-shot
  environment setup. `install` gets the runtime plus the corpus/index build
  toolchain (`--without dev`, since Poetry installs the dev *group* by default);
  `install-dev` adds pytest/ruff/pre-commit/ty/detect-secrets via `--all-extras`.
  Both run `install-model`, which downloads the `en_core_web_sm` spaCy model —
  a GitHub-hosted wheel that cannot be declared as a normal dependency — and
  no-ops once present
- `pyproject.toml`: **`[build]` optional-dependency extra** (`diary-kg>=0.96.0`,
  `doc-kg>=0.21.1`) — the corpus/index build toolchain, kept out of the runtime
  deps because diary-kg pulls the full spaCy/thinc stack that the service, which
  only reads a pre-built index, never needs
- `Makefile`: explicit `.DEFAULT_GOAL := help`, so a bare `make` prints help
  rather than triggering a multi-GB install, and reordering targets cannot
  silently change the default

### Changed
- **The image is now self-contained** (`docker/Dockerfile`) — built
  `FROM python:3.12-slim` instead of extending `egsuchanek/kgrag-worker:latest`,
  mirroring the gutenberg_kg worker. That base is shared and corpus-agnostic:
  it bulk-installs `wheels/*.whl`, so this image inherited `gutenberg-kg`,
  `metabo-kg` and `kg-snapshot` it never imported, LanceDB it never read, and an
  unpinned `kg-rag`. CPU-only torch is now installed from the PyTorch CPU index
  in its own layer *before* the KG stack, so `sentence-transformers` cannot
  silently pull the CUDA wheel. **Image size 10.9 GB → 3.6 GB** (2.9 GB of
  `nvidia-*` plus 660 MB of `triton` removed from a container that reports
  `torch.cuda.is_available() == False`). `runpod` and `pillow` are now declared
  explicitly — both were arriving from the base, and `handler.py` imports
  `runpod` at module level while `chat.py` lazily imports `PIL`
- **`make build-index` / `make reindex` now run `poetry run diarykg`** — they
  previously invoked a *globally* installed `diarykg`, which was at
  `diary-kg 0.93.2` / `doc-kg 0.15.8` / `kgmodule-utils 0.4.3` while the
  container expected `0.96.0` / `0.21.1` / `0.10.0`. Since doc-kg 0.18.2 moved
  vectors to file-shaped `vectors.sqlite`, the stale global env silently emitted
  a LanceDB-era index the container could not open. The build toolchain now
  lives in the project venv, pinned by the `[build]` extra
- `Makefile`: `make test` and `make lint` call `poetry run pytest` / `poetry run
  ruff` rather than resolving via `PATH`, where a global install would win
- **KG pins updated to the current fleet floors** (matching kgrag:
  `kgmodule-utils>=0.8.0`, `doc-kg>=0.18.2`, `diary-kg>=0.93.2`):
  `docker/Dockerfile` + `docker-compose.yml` now pin `kgmodule-utils 0.9.0`,
  `diary-kg 0.93.4`, `doc-kg 0.19.1`; `pyproject.toml` floor bumped to
  `kgmodule-utils[synthesis,sqlite-vec]>=0.9.0` (lock regenerated)
- **Worker vector store ported from LanceDB to sqlite-vec**
  (`docker/handler.py`). doc-kg ≥0.18 retires LanceDB: a fresh
  `diarykg build` writes `.diarykg/vectors.sqlite` (a sqlite-vec sidecar)
  instead of `.diarykg/lancedb/`. The handler now opens the store through
  `kg_utils.vector_backend.SqliteVecBackend` and registers `vectors_path`
  (not `lancedb_path`) with the KG registry. There is deliberately **no
  LanceDB fallback** — a pre-0.18 baked index gets a loud startup warning
  and empty results, not a silent legacy code path; lancedb is no longer
  imported anywhere in this repo (the package itself still lands in the
  image transitively: doc-kg/diary-kg hard-require it upstream). Search
  results and scores are unchanged (`_distance` is cosine in both stores).
  **The baked `.diarykg/` index must be rebuilt** (`make build-index`)
  before the next `make build-image`.
- `docker/Dockerfile`: the pip install now pulls the
  `kgmodule-utils[sqlite-vec]` extra — `sqlite-vec` is optional upstream, so
  a plain install cannot read `vectors.sqlite` without it
- **Worker retrieval is now semantic-first** (`docker/handler.py`). Queries rank
  chunks by their *own* cosine distance via a direct LanceDB search
  (`metric("cosine")`, chunk/section pre-filter) instead of the KGRAG
  orchestrator's graph-hop expansion, which let chunks inherit a flat seed score
  from graph-expanded neighbours. Clean passage text and diary timestamps are
  hydrated from SQLite (the LanceDB `text` column holds prefixed embed-text, not
  the clean passage). Mirrors the gutenberg_kg worker change.
- `docker/chat.py`: **Resolution picker now actually drives the render size** —
  the chosen preset is sent as `size` to the image backend (it was shown in the
  caption but never sent, so every render came back at 1536×1024). The aspect
  ratio selector is removed; images are fixed at 3:2 with resolution presets,
  matching the gutenberg_kg chat UI
- `docker/chat.py`: theme-aware hit cards and score bars — hardcoded dark-theme
  hex colours replaced with Streamlit theme variables (`var(--text-color)`,
  `var(--secondary-background-color)`); previews widened to 220 chars
- `docker/Dockerfile`: `kgmodule-utils[synthesis]` pinned to `0.4.3` via
  `KGMODULE_UTILS_VERSION` build arg (carries the image-size fix needed by the
  resolution picker); `uvicorn` dropped from the container install (the image
  server runs on the host, not in the container)
- `pyproject.toml`: `kgmodule-utils[synthesis]` floor bumped to `>=0.4.3`
- `Makefile` / `docker-compose.yml`: `make query` and the compose header comment
  now send `corpus="diary"` (the handler never accepted `"pepys"`)
- `.gitignore`: ignore `.vscode/`

### Fixed
- **Worker crash-looped at registry bootstrap** with
  `TypeError: KGEntry.__init__() got an unexpected keyword argument
  'vectors_path'`. `handler.py` passes `vectors_path=`, which exists only from
  `kg-rag 0.11.0`, but `kg-rag` was never pinned — it came from the base image
  at `0.7.0`, which predates the field and offers only `lancedb_path`. Now
  pinned via a `KG_RAG_VERSION` build arg. The Streamlit container stayed *up*
  while the worker died, so the stack looked healthy from `docker ps`
- **`make build-index` failed outright** with `spaCy model not found`: nothing
  installed `en_core_web_sm`, and `diary-kg` was absent from the project venv
  entirely despite `make build-corpus` calling `poetry run diary-transformer`

### Removed
- **Dependency on the `egsuchanek/kgrag-worker` base image** — with it go
  `gutenberg-kg`, `metabo-kg` and `kg-snapshot` (never imported here), the
  entire CUDA stack, and LanceDB. `lancedb` is now absent from the image
  altogether, superseding the note above that it "still lands in the image
  transitively" — that was true only of the old base
- **KGRAG orchestrator from the worker query path** — `handler.py` no longer
  initialises `KGRAG`; retrieval is served directly from the LanceDB table
- `docker/image_gen.py`: `vlm_rewrite()`, `generate_via_server()`, and
  `generate_auto()` removed — these paths moved to
  `kg_utils.synthesis.TextSynthesizer` / `ImageSynthesizer` in the kgmodule-utils
  migration; the module is now local-generation only (used by `image_server.py`)

---

## [0.3.0] — 2026-06-06

### Added
- `docker/image_gen.py` (new): image-generation module — `generate()` runs Flux2Klein locally via mflux (Apple Silicon), `generate_via_server()` calls a running mflux-serve HTTP instance, `generate_auto()` picks the right path automatically; `vlm_rewrite()` rewrites corpus prose into a visual scene description via a local VLM before passing it to FLUX
- `docker/image_server.py` (new): FastAPI/uvicorn wrapper around `image_gen.generate()` — keeps Flux2Klein loaded in-process between requests (no per-request cold-start), exposes OpenAI-compatible `/v1/models` and `/v1/images/generations`; replaces the `uvx mflux-serve` subprocess dependency
- `docker/chat.py`: **🎨 Render response** per-result button — sends diary passages through `vlm_rewrite()` (VLM prompt rewrite) then to the local FLUX image server; inline aspect-ratio picker (3:2, 16:9, 1:1, 4:3, 9:16, 2:3); rendered image displayed directly in the chat
- `docker/handler.py`: `op=imagine` operation — proxies image-generation requests to `IMAGE_ENDPOINT` (mflux-serve); accepts `prompt`, `aspect_ratio`, `steps`, and `seed`
- `Makefile`: `image-server` target (starts FLUX server as a background process on `:8090`); `up` target (one-shot launch for worker + chat + image server together); `$(COMPOSE)` variable to avoid repeating the compose file path; `down` alias for `stop`
- `docker/chat.py`, `docker/handler.py`: result cards now show the **actual diary passage**, not a truncated summary — the worker attaches each hit's full source text (`_attach_content` reads `nodes.text` in one batched query) and the UI shows a 200-char word-boundary preview that expands to the full entry (📖 Full entry). No extra text is stored; the text already lived in the DiaryKG store
- `docker/chat.py`: **💾 Save result** — download any answer (question, synthesized answer, and all source passages with scores) as a Markdown file
- `docker/chat.py`: **🗑️ Clear** button in the main pane (beside the title) in addition to the sidebar; the page re-runs after each answer so it appears reliably
- `docker/chat.py`, `docker/handler.py`: in-app **model picker** — the chat sidebar shows a dropdown of synthesis models pulled live from the worker (`{"op": "models"}` → the backend's `/v1/models`), and the chosen model is sent per-request via a new `model` override. The assistant turn shows which model produced the answer. Switch models with no restart or config edit
- `docker/handler.py`: `SYNTH_MAX_K` environment variable (default 12) — caps the number of diary snippets fed to LLM synthesis so a large display-`k` can't overflow the model's context window (Ollama defaults to `num_ctx=4096`; oMLX/vLLM are larger but finite). Retrieval/display `k` is unaffected
- `docker/handler.py`: `chat_template_kwargs.enable_thinking=false` is now sent alongside `think:false` to suppress Qwen3 reasoning where the backend supports it — oMLX/vLLM honour `chat_template_kwargs`, Ollama honours `think`; each ignores the field it doesn't recognise (the `<think>` strip remains a backstop). On hybrid *thinking* models this toggle is only best-effort, which is why the default model is a non-thinking Instruct variant (see below)
- `.secrets.baseline`: detect-secrets baseline (the pre-commit hook referenced it but the file was missing)
- `docker/handler.py`, `docker/chat.py`: **search and synthesis timing** — the worker now returns `search_ms` and `synthesis_ms` in every query response; the chat UI displays them in the result caption (`📊 N passages · search X ms · synthesis Y ms`)
- `docker/chat.py`: **VLM rewrite and image generation timings** shown in per-render captions (`🎨 Prompt: … · VLM X ms` and `🖼️ model · Resolution · WxH · X ms`)
- `docker/chat.py`: **🖼️ Resolution** sidebar selectbox — Preview (768×512), Standard (1152×768), Full (1536×1024); chosen resolution drives the FLUX request size and is shown in the image caption
- `docker/image_server.py`: `filepath` response format — when `response_format=filepath`, the server saves the PNG to `IMAGE_OUTPUT_DIR` (default `/tmp/pepys_images`) and returns the path instead of base64; `IMAGE_OUTPUT_DIR` env var configures the output directory

### Changed
- `docker/Dockerfile`: now installs `mflux`, `streamlit`, `httpx`, `openai`, and `uvicorn` in the image; copies `chat.py` and `image_gen.py` alongside `handler.py` so the container can serve or generate images
- `docker/docker-compose.yml`: `IMAGE_ENDPOINT` env var forwarded to both worker and chat services; `extra_hosts: host.docker.internal:host-gateway` added so containers can reach the host-side image server
- `docker/chat.py`: worker URL and secret now sourced from `KGRAG_ENDPOINT` / `HANDLER_SECRET` environment variables instead of sidebar text inputs — simplifies the UI and avoids exposing connection details
- `pyproject.toml`: added `mflux`, `openai`, `uvicorn`, and `fastapi` to project dependencies
- Synthesis backend default model is now `Qwen3-30B-A3B-Instruct-2507-MLX-4bit` (`docker/handler.py`, `docker/.env.example`, `docker/docker-compose.yml`), replacing `Qwen3-4B-Instruct-2507-MLX-8bit` — a larger MoE for higher-quality answers, and a non-thinking Instruct model so reasoning traces never leak into the response
- `docker/chat.py`: source passages are now collapsed by default once an answer is synthesized — the answer is the result, the passages are the supporting evidence
- Default corpus is now `diary` (`docker/chat.py` queries it; the worker accepts `diary`/`all`); the `pepys` corpus name was removed
- `docker/chat.py`: sidebar defaults — "Results" `8 → 10` (max also raised `20 → 50`), "Min score" (similarity) `0.0 → 0.5`
- `IMAGE_STEPS` env var unified — previously `GUTENKG_IMAGE_STEPS` in `image_gen.py` / `image_server.py`, now `IMAGE_STEPS` everywhere (`handler.py`, `docker-compose.yml`, `.env.example`); `docker/chat.py` reads `IMAGE_STEPS` and forwards it as `num_inference_steps` per-request so a single env var controls all three services
- `.pre-commit-config.yaml`: `mypy` hook retargeted from the nonexistent `src/` to `docker/` (repo has no `src/` layout)
- `.gitignore`: removed unneeded entries (cleanup)

### Fixed
- `docker/chat.py`: the chat UI no longer crashes with `'str' object has no attribute 'get'` on a worker `FAILED` response — the JSON-string error payload is decoded and the real message is shown

### Removed
- `docker/handler.py`: the `pepys` corpus alias (use `diary`)
- `.pre-commit-config.yaml`: `pytest` hook (project has no test suite) and `pylint` hook (not installed, no config, redundant with ruff)
- `analysis/pepys_enriched_full_run_summary.md`, `analysis/pepys_enriched_full_mpnet_embeddings_run_summary.md`: stale run summaries (cleanup)

---

## [0.1.1] — 2026-06-03

### Added
- `docs/USER_GUIDE.md`: Non-technical walkthrough of the Pepys chat app — starting it, asking questions, reading passages and relevance bars, sidebar settings, and enabling written answers
- `docs/API.md`: Developer-facing HTTP reference — endpoint, request/response schema, parameter table, examples, and LLM synthesis configuration
- `Makefile`: `make serve-llm` target to start an oMLX synthesis backend on `:8080` (8000 is reserved for the worker)
- `docker/handler.py`, `docker/docker-compose.yml`, `docker/.env.example`: `VLLM_API_KEY` bearer-token support for OpenAI-compatible synthesis endpoints
- `README.md`: "Who was Samuel Pepys?" introduction and a Documentation section linking the User Guide and API Reference

### Changed
- Synthesis backend now defaults to oMLX (`http://host.docker.internal:8080`, model `Qwen3-4B-Instruct-2507-MLX-8bit`) instead of Ollama; Ollama remains documented as a cross-platform alternative
- `docker/handler.py`: Auth header is now driven by `VLLM_API_KEY` (sent only when set) rather than the hardcoded `RUNPOD_API_KEY`
- `README.md`: Slimmed to lead with the chat app; API reference and synthesis details moved into `docs/`

### Removed
- `docker/handler.py`, `docker/docker-compose.yml`, `docker/.env.example`: `RUNPOD_API_KEY` environment variable, replaced by `VLLM_API_KEY`

---

## [0.1.0] — 2026-06-02

### Added
- `README.md`: Docker Hub fast path as primary quick start — `docker pull` + `docker run`, no build required
- `README.md`: Badges (Python, License, Version, Docker Hub, Poetry, Zenodo DOI placeholder), author attribution, cover image
- `assets/pepys_writing.jpg`: AI-generated Baroque cover image (1080×607, 146 KB) — Pepys writing by candlelight, Great Fire through the window, knowledge graph constellation
- `assets/pepys_writing.png`: Full-resolution master (1536×864, 2 MB)
- `docs/BUILDING.md`: Build-from-source instructions (index, Docker image, NLP corpus) moved out of README
- `pyproject.toml`: PEP 621 project metadata, Elastic-2.0 license, slim deps (httpx, PyYAML, rich, streamlit only)
- `poetry.lock`, `poetry.toml`: Dependency lockfile and in-project venv config
- `LICENSE`: Elastic License 2.0
- `.pre-commit-config.yaml`: Pre-commit hooks
- `docker/handler.py`: DiaryKG instance at startup for synthesis; `_synthesize()` now uses `DiaryKG.pack()` for full entry text instead of truncated KGRAG summaries
- `docker/chat.py`: 8 suggested queries in sidebar; clicking fires the search immediately via `pending_query` session state
- All `.py` files: EL2.0 copyright header (`© 2026 Eric G. Suchanek, PhD — Flux-Frontiers`)

### Changed
- `README.md`: Reorganised — Docker fast path leads, build instructions moved to `docs/BUILDING.md`
- `docker/handler.py`: Synthesis context upgraded from truncated summaries to full `DiaryKG.pack()` content
- `scripts/`: Pruned to three pipeline-relevant files (`pepys_proper_parse.py`, `topic_classifier.py`, `analyze_sentence_structure.py`)

### Removed
- `docs/COMPLETE_TECHNICAL_ARTICLE.md`: Canonical home is `diary_kg` repo
- `docs/PIPELINE_TECHNICAL_DISCLOSURE.md`: Canonical home is `diary_kg` repo
- `docs/nlp_ingestion_workflow.md`: Canonical home is `diary_kg` repo
- `docs/pipeline_article.md`: Duplicate of COMPLETE_TECHNICAL_ARTICLE.md, belongs in `diary_kg`
- `scripts/hindsight_analysis.py`, `scripts/diary_transformer_example.py`, `scripts/process_logo.py`, `scripts/analyze_pepys_entities.py`: Removed non-Pepys or example scripts
