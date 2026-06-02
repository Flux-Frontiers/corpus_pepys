# Changelog

All notable changes to corpus_pepys are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
