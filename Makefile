.PHONY: help build-corpus build-index reindex build-image run stop chat query clean

CORPUS_SOURCE ?= data/pepys_enriched_full.txt
IMAGE_NAME    ?= corpus-pepys
QUERY         ?= Great Fire of London

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
# Phase 2b: Re-index only — skip ingest, rebuild SQLite + LanceDB
# from the existing .diarykg/corpus/ .md files.
#
# SIMILAR_TO edges are disabled (--no-similar is hardcoded in
# diarykg reindex). For a single-author diary corpus, all-pairs
# similarity produces ~5M low-signal edges — same-author vocabulary
# uniformity inflates cosine scores across unrelated entries. The
# HAS_TOPIC / HAS_CATEGORY graph and the LanceDB ANN index already
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
	docker compose -f docker/docker-compose.yml up -d
	@echo "Pepys KGRAG running on http://localhost:8000"

stop:
	docker compose -f docker/docker-compose.yml down

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
		-d '{"input":{"query":"$(QUERY)","corpus":"pepys","k":5}}' | python3 -m json.tool

# ------------------------------------------------------------------
# Cleanup
# ------------------------------------------------------------------
clean:
	rm -rf .diarykg/
	docker image rm $(IMAGE_NAME):latest 2>/dev/null || true
	@echo "Done. Cleaned."
