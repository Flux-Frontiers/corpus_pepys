.PHONY: help build-corpus build-index reindex build-image run stop down image-server chat up query serve-llm test lint clean

CORPUS_SOURCE ?= data/pepys_enriched_full.txt
IMAGE_NAME    ?= corpus-pepys
QUERY         ?= Great Fire of London
OMLX_PORT     ?= 8080
COMPOSE       = docker compose -f docker/docker-compose.yml
IMAGE_SERVER  = http://localhost:8090

help:
	@echo "corpus_pepys — Samuel Pepys DiaryKG"
	@echo ""
	@echo "  make build-corpus   Transform raw text → enriched corpus (pepys_clean.txt → enriched)"
	@echo "  make build-index    Full build: ingest + index from $(CORPUS_SOURCE)"
	@echo "  make reindex        Re-index only (skip ingest, use existing corpus .md files)"
	@echo "  make build-image    Build Docker image (requires .diarykg/ from build-index)"
	@echo "  make run            Start the KGRAG service on http://localhost:8000"
	@echo "  make stop           Stop the service"
	@echo "  make chat           Launch Streamlit chat UI (worker must be running)"
	@echo "  make query          Fire a test query (set QUERY='...' to override)"
	@echo "  make serve-llm      Start oMLX synthesis backend on http://localhost:$(OMLX_PORT)"
	@echo "  make clean          Remove generated index and image"

# ------------------------------------------------------------------
# Phase 1: NLP enrichment — parse + transform raw diary text
# ------------------------------------------------------------------
build-corpus:
	@echo "Running DiaryTransformer on data/pepys_clean.txt ..."
	poetry run diary-transformer transform \
		data/pepys_clean.txt \
		data/pepys_enriched_full.txt \
		--topics-file config/pepys_only_topics.yaml \
		--restart \
		--batch-size 0
	@echo "Done. Enriched corpus: data/pepys_enriched_full.txt"

# ------------------------------------------------------------------
# Phase 2a: Full build — ingest + index from source text
# ------------------------------------------------------------------
build-index:
	@echo "Building DiaryKG index from $(CORPUS_SOURCE) ..."
	diarykg build . \
		--source $(CORPUS_SOURCE) \
		--snapshot
	@echo "Done. Index written to .diarykg/"

# ------------------------------------------------------------------
# Phase 2b: Re-index only — skip ingest, rebuild SQLite + vectors.sqlite
# from the existing .diarykg/corpus/ .md files.
#
# SIMILAR_TO edges are disabled (--no-similar is hardcoded in
# diarykg reindex). For a single-author diary corpus, all-pairs
# similarity produces ~5M low-signal edges — same-author vocabulary
# uniformity inflates cosine scores across unrelated entries. The
# HAS_TOPIC / HAS_CATEGORY graph and the sqlite-vec index already
# capture thematic structure more cleanly.
# ------------------------------------------------------------------
reindex:
	@test -d .diarykg/corpus || (echo "ERROR: .diarykg/corpus not found — run 'make build-index' first" && exit 1)
	@echo "Reindexing from existing corpus (no SIMILAR_TO scan) ..."
	diarykg reindex .
	@echo "Done. Index rebuilt in .diarykg/"

# ------------------------------------------------------------------
# Phase 3: Docker image — bakes .diarykg/ into the image
# ------------------------------------------------------------------
build-image:
	@test -d .diarykg || (echo "ERROR: .diarykg/ not found — run 'make build-index' first" && exit 1)
	docker build -f docker/Dockerfile -t $(IMAGE_NAME):latest .
	@echo "Done. Image built: $(IMAGE_NAME):latest"

# ------------------------------------------------------------------
# Run / stop
# ------------------------------------------------------------------
run:
	$(COMPOSE) up -d pepys-worker
	@echo "Pepys KGRAG running on http://localhost:8000"

down:
	$(COMPOSE) --profile chat down
	-pkill -f image_server.py 2>/dev/null || true

image-server:
	@if [ ! -x .venv-image/bin/python ]; then \
		echo "Creating .venv-image for isolated image dependencies ..."; \
		python3 -m venv .venv-image; \
	fi
	@.venv-image/bin/python -m pip install --quiet --upgrade pip
	@.venv-image/bin/python -m pip install --quiet -r docker/requirements-image.txt
	@echo "Starting FLUX image server on $(IMAGE_SERVER) (background, .venv-image) ..."
	MFLUX_SERVER_HOST=0.0.0.0 .venv-image/bin/python docker/image_server.py &

up:
	@echo "Starting worker + chat (Docker) ..."
	$(COMPOSE) --profile chat up -d
	@echo "Starting FLUX image server in background ..."
	$(MAKE) image-server
	@echo ""
	@echo "Worker:       http://localhost:8000"
	@echo "Image server: $(IMAGE_SERVER)"
	@echo "Chat UI:      http://localhost:8501"

# ------------------------------------------------------------------
# Chat UI — Streamlit frontend against the running worker
# ------------------------------------------------------------------
chat:
	streamlit run docker/chat.py

# ------------------------------------------------------------------
# Smoke test
# ------------------------------------------------------------------
query:
	@echo "Querying: $(QUERY)"
	curl -s -X POST http://localhost:8000/runsync \
		-H "Content-Type: application/json" \
		-d '{"input":{"query":"$(QUERY)","corpus":"diary","k":5}}' | python3 -m json.tool

# ------------------------------------------------------------------
# Optional LLM synthesis backend — oMLX (Apple Silicon, OpenAI-compatible).
# Runs on :$(OMLX_PORT) (8000 is taken by the worker). Uses oMLX's own
# persisted model directory; override with OMLX_PORT=... if needed.
# Point docker/.env at it: VLLM_ENDPOINT_URL=http://host.docker.internal:$(OMLX_PORT)
# ------------------------------------------------------------------
serve-llm:
	@command -v omlx >/dev/null 2>&1 || (echo "ERROR: omlx not found — install from https://omlx.ai" && exit 1)
	@echo "Starting oMLX synthesis backend on http://localhost:$(OMLX_PORT) ..."
	omlx serve --port $(OMLX_PORT)

# ------------------------------------------------------------------
# Tests & lint
# ------------------------------------------------------------------
test:
	pytest

lint:
	ruff check docker/ scripts/
	ruff format --check docker/ scripts/

# ------------------------------------------------------------------
# Cleanup
# ------------------------------------------------------------------
clean:
	rm -rf .diarykg/
	docker image rm $(IMAGE_NAME):latest 2>/dev/null || true
	@echo "Done. Cleaned."
