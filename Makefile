.PHONY: help setup install install-dev install-model check-pins build-corpus build-index reindex build build-image build-all pull run stop down logs image-server sdxl-server sdxl-fetch image-server-optional chat chat-container up query serve-llm test lint clean

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
SDXL_SERVER   = http://localhost:8091

# ------------------------------------------------------------------
# Container runtime — RUNTIME=docker (default) or RUNTIME=apple.
# RUNTIME=apple drives Apple's native `container` CLI instead of Docker
# (Apple Silicon + macOS 26; no Docker Desktop). First-time / per-boot setup
# is automatic — build/run/up depend on `setup`, which installs the CLI
# if missing (Homebrew) and runs `container system start`.
# Same targets, one extra variable:
#   make setup      RUNTIME=apple   — install `container` CLI + start services
#   make build      RUNTIME=apple   — build with `container build`
#   make run        RUNTIME=apple   — worker on :8000 (idempotent)
#   make up         RUNTIME=apple   — worker + chat UI (+ image server if supported)
#   make down       RUNTIME=apple   — stop/delete containers + image server
#   make logs       RUNTIME=apple   — follow worker logs
#   make clean      RUNTIME=apple   — remove index + image
# Per-container VM sizing (overridable): WORKER_MEM=8g WORKER_CPUS=6 CHAT_MEM=4g
#   make run RUNTIME=apple WORKER_MEM=12g
# See docs/APPLE_CONTAINERS.md for setup and caveats, and docs/DOCKER.md for
# the default Docker path (`make build-all` builds under both runtimes at once).
# ------------------------------------------------------------------
RUNTIME ?= docker

# Which runtimes are actually present, for `make build-all` and for the help
# text. Both are cheap `command -v` probes, evaluated once.
HAVE_DOCKER := $(shell command -v docker >/dev/null 2>&1 && echo 1)
HAVE_APPLE  := $(shell command -v container >/dev/null 2>&1 && echo 1)

# ------------------------------------------------------------------
# Can this host run the local FLUX image server?
#
# `make image-server` builds .venv-image from docker/requirements-image.txt,
# which installs mflux. mflux is not portable: on macOS it needs Apple MLX
# (arm64 only), on Linux it pulls mlx[cuda13] and so needs an NVIDIA GPU with
# CUDA 13, and it publishes no Windows wheel at all. On an ordinary x86 Docker
# host the pip install fails, and it used to take `make up` down with it —
# after the worker and chat had already started, so the stack looked broken
# when only the optional image backend was unavailable.
#
# Set FORCE_IMAGE_SERVER=1 to assert support anyway — the escape hatch for a
# CUDA 13 Linux box, which mflux does support but this probe cannot detect.
# ------------------------------------------------------------------
MFLUX_OK := $(shell \
  if [ "$(FORCE_IMAGE_SERVER)" = "1" ]; then echo 1; \
  elif [ "$$(uname -s 2>/dev/null)" = "Darwin" ] && [ "$$(uname -m 2>/dev/null)" = "arm64" ]; then echo 1; \
  fi)

# ------------------------------------------------------------------
# Image backend for `make up`: flux (FLUX.2 / mflux) or sdxl (SDXL-Lightning).
#
# The default is conditional, so image generation works on every platform
# rather than only Apple Silicon:
#   flux  — docker/image_server.py, :8090, mflux. Fastest, but see above.
#   sdxl  — docker/sdxl_server.py,  :8091, diffusers. Resolves cuda -> mps ->
#           cpu, so it runs anywhere; slow on plain CPU but it works.
#
#   make up                     → flux where mflux runs, sdxl everywhere else
#   make up IMAGE_BACKEND=sdxl  → force SDXL-Lightning (worker repointed)
#   make up IMAGE_BACKEND=flux  → force FLUX.2; errors if mflux cannot run here
#
# On Apple Silicon nothing changes: flux stays the default.
# ------------------------------------------------------------------
IMAGE_BACKEND ?= $(if $(MFLUX_OK),flux,sdxl)
ifeq ($(IMAGE_BACKEND),sdxl)
IMAGE_TARGET   = sdxl-server
IMAGE_URL      = $(SDXL_SERVER)
IMG_ENDPOINT   = http://host.docker.internal:8091
else
IMAGE_TARGET   = image-server
IMAGE_URL      = $(IMAGE_SERVER)
IMG_ENDPOINT   = http://host.docker.internal:8090
endif

# Apple `container` settings (RUNTIME=apple only). Each container is its own
# VM — memory is an explicit upper bound, not shared with the host like Docker
# Desktop's single big VM. Lazily allocated, so these do not pin that much RAM
# up front. Measured via `container stats` with torch + embedder + the full
# 41K-node graph loaded: worker idles at ~950 MiB and peaks at ~1.02 GiB under
# 8-way concurrent k=50 queries; chat idles at ~100 MiB. Defaults below give
# ~2x headroom over the observed worker peak and ~5x over chat.
WORKER_NAME  = pepys-worker
CHAT_NAME    = pepys-chat
WORKER_MEM  ?= 2g
WORKER_CPUS ?= 6
CHAT_MEM    ?= 512m

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
	@echo "  make build          Build the image for the selected runtime"
	@echo "  make build-all      Build for every runtime installed on this machine"
	@echo "  make pull           Pull the published image from Docker Hub (no local build needed)"
	@echo "  make run            Start the KGRAG service on http://localhost:8000"
	@echo "  make up             Worker + chat UI (+ image server where supported)"
	@echo "  make stop           Stop the service"
	@echo "  make down           Stop and remove the containers"
	@echo "  make chat           Launch Streamlit chat UI (worker must be running)"
	@echo "  make query          Fire a test query (set QUERY='...' to override)"
	@echo "  make serve-llm      Start oMLX synthesis backend on http://localhost:$(OMLX_PORT)"
	@echo "  make logs           Follow worker logs"
	@echo "  make clean          Remove generated index and image"
	@echo ""
	@echo "  Runtime: RUNTIME=docker (default) or RUNTIME=apple for Apple's"
	@echo "  native 'container' CLI (e.g. make up RUNTIME=apple)."
	@echo "  Current runtime: $(RUNTIME)"
	@echo "  Detected:  docker=$(if $(HAVE_DOCKER),yes,no)  container=$(if $(HAVE_APPLE),yes,no)"
	@echo "  Image backend: $(IMAGE_BACKEND) ($(IMAGE_URL))   [flux needs mflux: $(if $(MFLUX_OK),yes,no)]"
	@echo ""
	@echo "  Synthesis is optional. Ollama works everywhere; oMLX is the faster"
	@echo "  Apple-Silicon option. See docs/API.md."

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
# spaCy model. Dev tooling is excluded simply by not asking for it: the dev
# tools live in the PEP 621 `dev` extra, and extras are opt-in. (This used to
# pass `--without dev` for a Poetry dev *group*, which Poetry installs by
# default — that group is gone, and naming it now only earns a warning.)
install:
	poetry install --extras build
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
	@if [ "$(MFLUX_OK)" != "1" ]; then \
		echo "ERROR: the FLUX image server cannot run on this host."; \
		echo "  mflux needs Apple MLX (macOS arm64), or mlx[cuda13] on a Linux box"; \
		echo "  with an NVIDIA GPU. It ships no Windows wheel."; \
		echo "  Use the portable backend instead:  make up IMAGE_BACKEND=sdxl"; \
		echo "  (SDXL-Lightning resolves cuda -> mps -> cpu, so it runs anywhere.)"; \
		echo "  Or re-run with FORCE_IMAGE_SERVER=1 on a CUDA 13 Linux host."; \
		exit 1; \
	fi
	@if [ ! -x .venv-image/bin/python ]; then \
		echo "Creating .venv-image for isolated image dependencies ..."; \
		python3 -m venv .venv-image; \
	fi
	@.venv-image/bin/python -m pip install --quiet --upgrade pip
	@.venv-image/bin/python -m pip install --quiet -r docker/requirements-image.txt
	@echo "Starting FLUX image server on $(IMAGE_SERVER) (background, .venv-image) ..."
	MFLUX_SERVER_HOST=0.0.0.0 .venv-image/bin/python docker/image_server.py &

# The portable image backend. Its own venv, separate from .venv-image: mflux and
# diffusers pin conflicting transformers ranges, so the two cannot share one.
# Binds 0.0.0.0 so an Apple container VM can reach it over the vmnet.
#
# First run downloads ~7 GB of weights from HuggingFace and caches them under
# ~/.cache/huggingface. `make sdxl-fetch` does that step on its own if you would
# rather not fold it into a `make up`.
sdxl-server:
	@if [ ! -x .venv-sdxl/bin/python ]; then \
		echo "Creating .venv-sdxl for isolated diffusers dependencies ..."; \
		python3 -m venv .venv-sdxl; \
	fi
	@.venv-sdxl/bin/python -m pip install --quiet --upgrade pip
	@.venv-sdxl/bin/python -m pip install --quiet -r docker/requirements-sdxl.txt
	@echo "Starting SDXL-Lightning image server on $(SDXL_SERVER) (background, .venv-sdxl) ..."
	SDXL_SERVER_HOST=0.0.0.0 .venv-sdxl/bin/python docker/sdxl_server.py &

# Pre-download the SDXL weights without starting the server, so the first
# `make up` is not a silent multi-GB wait.
sdxl-fetch:
	@if [ ! -x .venv-sdxl/bin/python ]; then \
		echo "Creating .venv-sdxl for isolated diffusers dependencies ..."; \
		python3 -m venv .venv-sdxl; \
	fi
	@.venv-sdxl/bin/python -m pip install --quiet --upgrade pip
	@.venv-sdxl/bin/python -m pip install --quiet -r docker/requirements-sdxl.txt
	@echo "Fetching SDXL-Lightning weights (~7 GB, cached under ~/.cache/huggingface) ..."
	@.venv-sdxl/bin/python -c "import sys; sys.path.insert(0, 'docker'); import sdxl_server; sdxl_server._load_pipeline()"
	@echo "Done. Weights cached; SDXL_OFFLINE=1 will now work."

# ------------------------------------------------------------------
# `build-image` is the old name for `build`. Kept as an alias because the
# README, docs/BUILDING.md and the CHANGELOG all reference it, and because it
# is what anyone with muscle memory from before will type. `build` is the
# canonical name and matches gutenberg_kg.
# ------------------------------------------------------------------
build-image: build

# Build the image under EVERY runtime installed on this machine, rather than
# only the one RUNTIME selects. Useful on a Mac carrying both Docker Desktop
# and Apple's `container` CLI: the two keep separate image stores, so an image
# built by one is invisible to the other and `make run RUNTIME=apple` after a
# Docker build silently has nothing to run.
#
# Skips a runtime that is not installed rather than failing — on Linux there is
# no `container` CLI and that is not an error. Fails only if neither is present.
build-all:
	@if [ -z "$(HAVE_DOCKER)$(HAVE_APPLE)" ]; then \
		echo "ERROR: neither Docker nor Apple's 'container' CLI is installed."; \
		exit 1; \
	fi
	@if [ "$(HAVE_DOCKER)" = "1" ]; then \
		echo "==> Building with Docker ..."; \
		$(MAKE) --no-print-directory build RUNTIME=docker; \
	else \
		echo "==> Skipping Docker — not installed."; \
	fi
	@if [ "$(HAVE_APPLE)" = "1" ]; then \
		echo "==> Building with Apple container ..."; \
		$(MAKE) --no-print-directory build RUNTIME=apple; \
	else \
		echo "==> Skipping Apple container — not installed."; \
	fi

# What `make up` calls. Starting the image server is best-effort: it is an
# optional backend for one button in the chat UI, so a host that cannot run it
# gets a note, not a failed `up` with the worker and chat already running.
image-server-optional:
	@echo "Starting $(IMAGE_BACKEND) image server in background ..."
	-@$(MAKE) --no-print-directory $(IMAGE_TARGET) || \
		echo "WARNING: the image server did not start. Worker and chat are up; only the chat UI's 'Render response' button is affected."

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
  IMAGE_ENDPOINT=$$(printf '%s' "$${IMAGE_ENDPOINT:-$(IMG_ENDPOINT)}" | sed 's/host\.docker\.internal/$(APPLE_HOST_GW)/g')

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

build: check-pins setup
	@test -d .diarykg || (echo "ERROR: .diarykg/ not found — run 'make build-index' first" && exit 1)
	container build -f docker/Dockerfile -t $(IMAGE_NAME):latest .
	@echo "Done. Image built: $(IMAGE_NAME):latest  (runtime: apple)"

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
	  -e HANDLER_SECRET="$${HANDLER_SECRET:-}" \
	  -e IMAGE_ENDPOINT="$$IMAGE_ENDPOINT" \
	  -e IMAGE_STEPS="$${IMAGE_STEPS:-4}" \
	  $(IMAGE_NAME):latest \
	  streamlit run /app/chat.py --server.port 8501 --server.address 0.0.0.0
	@echo "Chat UI: http://localhost:8501"

up: chat-container
	@echo "Image backend: $(IMAGE_BACKEND)"
	@$(MAKE) --no-print-directory image-server-optional
	@echo ""
	@echo "Worker:       http://localhost:8000"
	@echo "Image server: $(IMAGE_URL)  ($(IMAGE_BACKEND))"
	@echo "Chat UI:      http://localhost:8501"
	@echo ""
	@echo "Run 'make down RUNTIME=apple' to shut down."

# `stop` halts the containers but keeps them (and the loaded index) around, so
# `make run` restarts without re-reading the graph and re-warming the embedder.
# `down` deletes them. Both are advertised in `help`; only `down` existed, so
# `make stop` failed with "No rule to make target".
stop:
	-container stop $(CHAT_NAME) $(WORKER_NAME) 2>/dev/null || true
	-pkill -f image_server.py 2>/dev/null || true
	-pkill -f sdxl_server.py 2>/dev/null || true

down:
	-container delete -f $(CHAT_NAME) $(WORKER_NAME) 2>/dev/null || true
	-pkill -f image_server.py 2>/dev/null || true
	-pkill -f sdxl_server.py 2>/dev/null || true

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

build: check-pins setup
	@test -d .diarykg || (echo "ERROR: .diarykg/ not found — run 'make build-index' first" && exit 1)
	docker build -f docker/Dockerfile -t $(IMAGE_NAME):latest .
	@echo "Done. Image built: $(IMAGE_NAME):latest  (runtime: docker)"

# docker-compose.yml refers to the image as $(IMAGE_NAME):latest (no registry
# prefix), so a bare `docker pull` of the Hub image leaves it invisible to
# `make run`/`make up` — compose would instead try to build it from source
# (and fail without a local .diarykg/). Retag it locally to close that gap.
pull:
	docker pull egsuchanek/corpus-pepys:latest
	docker tag egsuchanek/corpus-pepys:latest $(IMAGE_NAME):latest
	@echo "Tagged as $(IMAGE_NAME):latest — ready for 'make run' / 'make up'."

run:
	$(COMPOSE) up -d pepys-worker
	@echo "Pepys KGRAG running on http://localhost:8000"

# See the RUNTIME=apple branch above for why both `stop` and `down` exist.
stop:
	$(COMPOSE) --profile chat stop
	-pkill -f image_server.py 2>/dev/null || true
	-pkill -f sdxl_server.py 2>/dev/null || true

down:
	$(COMPOSE) --profile chat down
	-pkill -f image_server.py 2>/dev/null || true
	-pkill -f sdxl_server.py 2>/dev/null || true

logs:
	$(COMPOSE) logs -f pepys-worker

up:
	@echo "Starting worker + chat (Docker), image backend: $(IMAGE_BACKEND) ($(IMG_ENDPOINT)) ..."
	IMAGE_ENDPOINT=$(IMG_ENDPOINT) $(COMPOSE) --profile chat up -d
	@$(MAKE) --no-print-directory image-server-optional
	@echo ""
	@echo "Worker:       http://localhost:8000"
	@echo "Image server: $(IMAGE_URL)  ($(IMAGE_BACKEND))"
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
