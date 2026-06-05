# Changelog

All notable changes to corpus_pepys are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [0.2.0] — 2026-06-05

### Added
- `docker/chat.py`, `docker/handler.py`: in-app **model picker** — the chat sidebar shows a dropdown of synthesis models pulled live from the worker (`{"op": "models"}` → the backend's `/v1/models`), and the chosen model is sent per-request via a new `model` override. The assistant turn shows which model produced the answer. Switch models with no restart or config edit
- `docker/handler.py`: `SYNTH_MAX_K` environment variable (default 12) — caps the number of diary snippets fed to LLM synthesis so a large display-`k` can't overflow the model's context window (Ollama defaults to `num_ctx=4096`; oMLX/vLLM are larger but finite). Retrieval/display `k` is unaffected
- `docker/handler.py`: `chat_template_kwargs.enable_thinking=false` is now sent alongside `think:false` to suppress Qwen3 reasoning where the backend supports it — oMLX/vLLM honour `chat_template_kwargs`, Ollama honours `think`; each ignores the field it doesn't recognise (the `<think>` strip remains a backstop). On hybrid *thinking* models this toggle is only best-effort, which is why the default model is a non-thinking Instruct variant (see below)
- `.secrets.baseline`: detect-secrets baseline (the pre-commit hook referenced it but the file was missing)

### Changed
- Synthesis backend default model is now `Qwen3-30B-A3B-Instruct-2507-MLX-4bit` (`docker/handler.py`, `docker/.env.example`, `docker/docker-compose.yml`), replacing `Qwen3-4B-Instruct-2507-MLX-8bit` — a larger MoE for higher-quality answers, and a non-thinking Instruct model so reasoning traces never leak into the response
- `docker/chat.py`: "Results" slider maximum raised from 20 to 50
- `.pre-commit-config.yaml`: `mypy` hook retargeted from the nonexistent `src/` to `docker/` (repo has no `src/` layout)
- `.gitignore`: removed unneeded entries (cleanup)

### Removed
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
