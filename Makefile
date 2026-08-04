.PHONY: help setup install install-dev install-model check-pins build-corpus build-index reindex build-image run stop down logs image-server chat chat-container up query serve-llm test lint clean

# Bare `make` prints help rather than installing: a cold `make install` pulls
# torch + the spaCy stack, which is not what a stray keystroke should trigger.
# Declared explicitly so reordering targets can't silently change the default.
.DEFAULT_GOAL := help

CORPUS_SOURCE ?= data/pepys_enriched_full.txt
IMAGE_NAME    ?= corpus-pepys
QUERY         ?= Great Fire of London
OMLX_PORT     ?= 8080
COMPOSE       = docker compose -f docker/docker-compose.yml
IMAGE_SERVER  = http://localhost:8090

# ------------------------------------------------------------------
# Container runtime — RUNTIME=docker (default) or RUNTIME=apple.
# RUNTIME=apple drives Apple's native `container` CLI instead of Docker
# (Apple Silicon + macOS 26; no Docker Desktop). First-time / per-boot setup
# is automatic — build-image/run/up depend on `setup`, which installs the CLI
# if missing (Homebrew) and runs `container system start`.
# Same targets, one extra variable:
#   make setup      RUNTIME=apple   — install `container` CLI + start services
#   make build-image RUNTIME=apple  — build with `container build`
#   make run        RUNTIME=apple   — worker on :8000 (idempotent)
#   make up         RUNTIME=apple   — worker + chat UI + FLUX image server
#   make down       RUNTIME=apple   — stop/delete containers + image server
#   make logs       RUNTIME=apple   — follow worker logs
#   make clean      RUNTIME=apple   — remove index + image
# Per-container VM sizing (overridable): WORKER_MEM=8g WORKER_CPUS=6 CHAT_MEM=4g
#   make run RUNTIME=apple WORKER_MEM=12g
# See docs/APPLE_CONTAINERS.md for setup and caveats.
# ------------------------------------------------------------------
RUNTIME ?= docker

# Apple `container` settings (RUNTIME=apple only). Each container is its own
# VM — memory is an explicit upper bound, not shared with the host like Docker
# Desktop's single big VM, and the defaults are far too small for the worker
# (torch + embedder + 41K-node graph + 41K vectors). Lazily allocated, so 8g
# does not pin 8 GB of RAM.
WORKER_NAME  = pepys-worker
CHAT_NAME    = pepys-chat
WORKER_MEM  ?= 8g
WORKER_CPUS ?= 6
CHAT_MEM    ?= 4g

# Host reachability from containers. Apple's `container` supports Docker-style
# port publishing (`--publish`) as of CLI v1.1.0, so worker and chat ports are
# forwarded and reachable at localhost, same as the Docker path. But
# host.docker.internal does NOT resolve inside these VMs, so anything pointing
# at the host — the oMLX/Ollama LLM, the FLUX image server, chat->worker — must
# use the vmnet gateway instead. Getting it wrong fails silently: the worker
# simply cannot reach the LLM, so you get answers with no synthesis and no
# error. So detect it from the live `default` network, and treat the constant
# purely as a cold-start fallback for when the runtime is not yet running.
#
# The fallback is 192.168.64.1 because that is what the `container-network-vmnet`
# plugin actually allocates — macOS's vmnet framework defaults to
# 192.168.64.0/24. Verified on CLI 1.1.0 against a network created fresh by
# `container system start`, not a leftover from an older CLI:
#
#   $ container network list
#   NETWORK  SUBNET
#   default  192.168.64.0/24
#
# gutenberg_kg carries 192.168.65.1 here with a comment claiming CLI 1.1.0 moved
# to 192.168.65.0/24. That is wrong — 192.168.65.x is *Docker Desktop's* gateway
# subnet, which is where the number appears to have come from. Do not copy it
# back. Override per-machine with `make APPLE_HOST_GW=… …` if yours differs.
#
# Host services must bind 0.0.0.0, not 127.0.0.1, to be reachable over the vmnet.
ifeq ($(RUNTIME),apple)
APPLE_HOST_GW ?= $(or $(shell container network inspect default 2>/dev/null | sed -n 's/.*"ipv4Gateway" : "\([0-9.]*\)".*/\1/p' | head -1),192.168.64.1)
else
APPLE_HOST_GW ?= 192.168.64.1
endif

help:
	@echo "corpus_pepys — Samuel Pepys DiaryKG"
	@echo ""
	@echo "  make install        Setup: runtime + NLP build toolchain + spaCy model"
	@echo "  make install-dev    As above, plus dev tools (pytest, ruff, pre-commit)"
	@echo "  make build-corpus   Transform raw text → enriched corpus (pepys_clean.txt → enriched)"
	@echo "  make build-index    Full build: ingest + index from $(CORPUS_SOURCE)"
	@echo "  make reindex        Re-index only (skip ingest, use existing corpus .md files)"
	@echo "  make check-pins     Verify lock/Dockerfile/compose KG pins agree"
	@echo "  make build-image    Build Docker image (requires .diarykg/ from build-index)"
	@echo "  make run            Start the KGRAG service on http://localhost:8000"
	@echo "  make stop           Stop the service"
	@echo "  make chat           Launch Streamlit chat UI (worker must be running)"
	@echo "  make query          Fire a test query (set QUERY='...' to override)"
	@echo "  make serve-llm      Start oMLX synthesis backend on http://localhost:$(OMLX_PORT)"
	@echo "  make logs           Follow worker logs"
	@echo "  make clean          Remove generated index and image"
	@echo ""
	@echo "  Add RUNTIME=apple to any container target to use Apple's native"
	@echo "  'container' CLI instead of Docker (e.g. make up RUNTIME=apple)."
	@echo "  Current runtime: $(RUNTIME)"

# ------------------------------------------------------------------
# Phase 0: build toolchain
#
# The corpus/index build tools live in the PROJECT venv, pinned by the
# [build] extra to the same versions docker/Dockerfile installs. They are
# deliberately NOT taken from a globally-installed diarykg: the index format
# is version-sensitive, and a stale global env silently produces an index the
# container cannot read.
# ------------------------------------------------------------------
# One-shot setup for a fresh clone: runtime + the NLP build toolchain + the
# spaCy model. Dev tooling is deliberately excluded — `--without dev` is
# required because Poetry installs the dev *group* by default.
install:
	poetry install --extras build --without dev
	@$(MAKE) --no-print-directory install-model
	@echo "Done. Environment ready."

# Everything in `install` plus the dev tooling (pytest, ruff, pre-commit,
# detect-secrets, ty). --all-extras covers the build + dev extras, and the
# Poetry dev *group* comes along by default, so no --with is needed.
# Required before `make test` / `make lint`: those call pytest and ruff, which
# `make install` deliberately leaves out.
install-dev:
	poetry install --all-extras
	@$(MAKE) --no-print-directory install-model
	@echo "Done. Dev environment ready."

# The index is built here by the [build] extra and read by the container. Those
# two must agree — doc-kg >=0.18.2 changed the vector store layout, so a builder
# older than the runtime emits an index the container cannot open, and it fails
# silently as empty results. `poetry update` moves the lock without touching the
# Dockerfile ARGs; this is what catches that. A prerequisite of build-image.
check-pins:
	@poetry run python scripts/check_pins.py

# en_core_web_sm is a GitHub-hosted wheel, not a PyPI package, so it cannot be
# declared as a normal dependency. No-op once present.
install-model:
	@poetry run python -c "import en_core_web_sm" 2>/dev/null \
		|| (echo "Downloading spaCy model en_core_web_sm ..." \
		    && poetry run python -m spacy download en_core_web_sm)

# ------------------------------------------------------------------
# Phase 1: NLP enrichment — parse + transform raw diary text
# ------------------------------------------------------------------
build-corpus: install-model
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
build-index: install-model
	@echo "Building DiaryKG index from $(CORPUS_SOURCE) ..."
	poetry run diarykg build . \
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
	poetry run diarykg reindex .
	@echo "Done. Index rebuilt in .diarykg/"

# ------------------------------------------------------------------
# The FLUX image server always runs on the HOST (mflux needs native MLX, not
# a Linux VM), for both runtimes. It binds 0.0.0.0 so an Apple container VM
# can reach it over the vmnet — 127.0.0.1 would be invisible from there.
# ------------------------------------------------------------------
image-server:
	@if [ ! -x .venv-image/bin/python ]; then \
		echo "Creating .venv-image for isolated image dependencies ..."; \
		python3 -m venv .venv-image; \
	fi
	@.venv-image/bin/python -m pip install --quiet --upgrade pip
	@.venv-image/bin/python -m pip install --quiet -r docker/requirements-image.txt
	@echo "Starting FLUX image server on $(IMAGE_SERVER) (background, .venv-image) ..."
	MFLUX_SERVER_HOST=0.0.0.0 .venv-image/bin/python docker/image_server.py &

ifeq ($(RUNTIME),apple)

# ------------------------------------------------------------------
# Apple `container` runtime (macOS 26, Apple Silicon).
# `setup` installs the CLI if needed and starts its services (the once-per-boot
# step); build-image/run depend on it, so a clean clone works out of the box.
# docker/.env is sourced explicitly below to mirror compose's automatic .env
# loading, then its host.docker.internal endpoints are rewritten to the vmnet
# gateway — without that, a .env pointing oMLX at host.docker.internal silently
# disables synthesis because the worker cannot resolve the name.
# ------------------------------------------------------------------

APPLE_REWRITE_ENDPOINTS = \
  VLLM_ENDPOINT_URL=$$(printf '%s' "$${VLLM_ENDPOINT_URL:-http://$(APPLE_HOST_GW):$(OMLX_PORT)/v1}" | sed 's/host\.docker\.internal/$(APPLE_HOST_GW)/g'); \
  OLLAMA_ENDPOINT=$$(printf '%s' "$${OLLAMA_ENDPOINT:-http://$(APPLE_HOST_GW):11434/v1}" | sed 's/host\.docker\.internal/$(APPLE_HOST_GW)/g'); \
  IMAGE_ENDPOINT=$$(printf '%s' "$${IMAGE_ENDPOINT:-http://$(APPLE_HOST_GW):8090}" | sed 's/host\.docker\.internal/$(APPLE_HOST_GW)/g')

# Idempotent host setup: install Apple's `container` CLI if missing (Homebrew,
# bottled — no sudo; otherwise point at the GitHub releases pkg) and start its
# services. `container system start` is a no-op when already running;
# --enable-kernel-install auto-answers the first-run prompt to download the
# guest kernel every container VM boots.
setup:
	@if ! command -v container >/dev/null 2>&1; then \
		if command -v brew >/dev/null 2>&1; then \
			echo "Installing Apple container CLI (brew install container) ..."; \
			brew install container; \
		else \
			echo "Apple 'container' CLI not found and Homebrew is unavailable."; \
			echo "Install the pkg from https://github.com/apple/container/releases, then re-run."; \
			exit 1; \
		fi; \
	fi
	@container system start --enable-kernel-install
	@echo "Apple container runtime ready."

build-image: check-pins setup
	@test -d .diarykg || (echo "ERROR: .diarykg/ not found — run 'make build-index' first" && exit 1)
	container build -f docker/Dockerfile -t $(IMAGE_NAME):latest .
	@echo "Done. Image built: $(IMAGE_NAME):latest"

# Idempotent like `compose up`: a running worker is left alone (it takes a
# while to load the index and embedder), a stopped or stale one is replaced.
run: setup
	@if container list --quiet 2>/dev/null | grep -qx "$(WORKER_NAME)"; then \
		echo "Worker already running at http://localhost:8000"; exit 0; \
	fi; \
	container delete -f $(WORKER_NAME) >/dev/null 2>&1 || true; \
	set -a; [ -f docker/.env ] && . docker/.env; set +a; \
	$(APPLE_REWRITE_ENDPOINTS); \
	container run --detach --name $(WORKER_NAME) \
	  --memory $(WORKER_MEM) --cpus $(WORKER_CPUS) \
	  --publish 8000:8000 \
	  -e PEPYS_KG_ROOT=/workspace/pepys \
	  -e EMBED_MODEL=BAAI/bge-small-en-v1.5 \
	  -e HANDLER_SECRET="$${HANDLER_SECRET:-}" \
	  -e RUNPOD_LOG_LEVEL="$${RUNPOD_LOG_LEVEL:-INFO}" \
	  -e VLLM_ENDPOINT_URL="$$VLLM_ENDPOINT_URL" \
	  -e VLLM_MODEL="$${VLLM_MODEL:-Qwen3-4B-Instruct-2507-MLX-8bit}" \
	  -e VLLM_API_KEY="$${VLLM_API_KEY:-}" \
	  -e OLLAMA_ENDPOINT="$$OLLAMA_ENDPOINT" \
	  -e OPENAI_API_KEY="$${OPENAI_API_KEY:-}" \
	  -e IMAGE_ENDPOINT="$$IMAGE_ENDPOINT" \
	  -e IMAGE_STEPS="$${IMAGE_STEPS:-4}" \
	  $(IMAGE_NAME):latest \
	  python -u handler.py --rp_serve_api --rp_api_host 0.0.0.0
	@echo "Pepys KGRAG running on http://localhost:8000"

# Chat reaches the worker at the vmnet gateway, where the worker's published
# 8000 is forwarded to the host — no container-to-container networking needed.
# (compose uses the service name `pepys-worker`, which does not resolve here.)
chat-container: run
	@container delete -f $(CHAT_NAME) >/dev/null 2>&1 || true
	@set -a; [ -f docker/.env ] && . docker/.env; set +a; \
	$(APPLE_REWRITE_ENDPOINTS); \
	container run --detach --name $(CHAT_NAME) \
	  --memory $(CHAT_MEM) \
	  --publish 8501:8501 \
	  -e KGRAG_ENDPOINT="http://$(APPLE_HOST_GW):8000" \
	  -e IMAGE_ENDPOINT="$$IMAGE_ENDPOINT" \
	  -e IMAGE_STEPS="$${IMAGE_STEPS:-4}" \
	  $(IMAGE_NAME):latest \
	  streamlit run /app/chat.py --server.port 8501 --server.address 0.0.0.0
	@echo "Chat UI: http://localhost:8501"

up: chat-container
	@echo "Starting FLUX image server in background ..."
	$(MAKE) image-server
	@echo ""
	@echo "Worker:       http://localhost:8000"
	@echo "Image server: $(IMAGE_SERVER)"
	@echo "Chat UI:      http://localhost:8501"
	@echo ""
	@echo "Run 'make down RUNTIME=apple' to shut down."

down:
	-container delete -f $(CHAT_NAME) $(WORKER_NAME) 2>/dev/null || true
	-pkill -f image_server.py 2>/dev/null || true

logs:
	container logs -f $(WORKER_NAME)

else

# ------------------------------------------------------------------
# Docker runtime (default) — docker compose drives worker + chat.
# ------------------------------------------------------------------

# Nothing to install — just verify the daemon is reachable.
setup:
	@command -v docker >/dev/null 2>&1 || { echo "Docker not found — install Docker Desktop, or use RUNTIME=apple."; exit 1; }
	@docker info >/dev/null 2>&1 || { echo "Docker daemon not running — start Docker Desktop, or use RUNTIME=apple."; exit 1; }
	@echo "Docker runtime ready."

build-image: check-pins
	@test -d .diarykg || (echo "ERROR: .diarykg/ not found — run 'make build-index' first" && exit 1)
	docker build -f docker/Dockerfile -t $(IMAGE_NAME):latest .
	@echo "Done. Image built: $(IMAGE_NAME):latest"

run:
	$(COMPOSE) up -d pepys-worker
	@echo "Pepys KGRAG running on http://localhost:8000"

down:
	$(COMPOSE) --profile chat down
	-pkill -f image_server.py 2>/dev/null || true

logs:
	$(COMPOSE) logs -f pepys-worker

up:
	@echo "Starting worker + chat (Docker) ..."
	$(COMPOSE) --profile chat up -d
	@echo "Starting FLUX image server in background ..."
	$(MAKE) image-server
	@echo ""
	@echo "Worker:       http://localhost:8000"
	@echo "Image server: $(IMAGE_SERVER)"
	@echo "Chat UI:      http://localhost:8501"

endif

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
# `poetry run` so these resolve inside the project venv rather than via PATH —
# a global pytest/ruff would otherwise silently run instead. Both require
# `make install-dev`; plain `make install` excludes the dev group.
test:
	poetry run pytest

lint:
	poetry run ruff check docker/ scripts/
	poetry run ruff format --check docker/ scripts/

# ------------------------------------------------------------------
# Cleanup
# ------------------------------------------------------------------
clean:
	rm -rf .diarykg/
ifeq ($(RUNTIME),apple)
	-container image rm $(IMAGE_NAME):latest 2>/dev/null || true
else
	-docker image rm $(IMAGE_NAME):latest 2>/dev/null || true
endif
	@echo "Done. Cleaned."
