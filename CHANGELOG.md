# Changelog

All notable changes to corpus_pepys are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

### Fixed

- **`tests/test_sdxl_server.py` tested the wrong thing.** Its import-deferral
  check asserted `"torch" not in sys.modules` — global interpreter state, which
  says nothing about what *this* module imports. It passed here only because
  torch is absent from this project's test environment, and that absence cannot
  distinguish "the module does not import torch" from "torch is not installed".
  The same test failed the moment it ran in `gutenberg_kg`, where torch arrives
  transitively via doc-kg and other test modules load it first.

  Replaced with a static check: parse the module source and assert no top-level
  import of torch, diffusers, uvicorn, huggingface_hub or safetensors. Function-
  level imports are deferred by construction and never appear in `tree.body`;
  `if TYPE_CHECKING:` blocks are skipped because they do not execute, and a
  companion test pins that the `TYPE_CHECKING` `import torch` is still present,
  so the check cannot be satisfied by deleting the type annotation. Verified in
  both directions with the heavy modules pre-seeded into `sys.modules`: all 44
  pass with them loaded, and reintroducing a module-scope `import torch` fails
  the check rather than passing vacuously.

Two passes. First a consistency audit against `gutenberg_kg` — Docker build,
chat UI, and dependency pins; the two repos serve the same stack from the same
worker contract, so everywhere they disagreed was either a bug here or a trap
waiting to become one. Then a runtime pass: Docker is the default and the only
option most people have, but `make up` broke on any non-Apple host and the docs
had drifted into presenting the Apple-Silicon setup as the normal one. Then a
third: porting `gutenberg_kg`'s SDXL-Lightning image server, so image generation
works on those hosts instead of merely being skipped.

### Added
- **`make pull`** — fetches the published image from Docker Hub and retags it
  as `corpus-pepys:latest`, the name `docker-compose.yml` expects. Without
  this, a bare `docker pull` left the image invisible to `make run`/`make up`,
  which would instead try (and fail) to build from source.

- **`docker/sdxl_server.py` (new)** — an SDXL-Lightning image server on `:8091`,
  ported from `gutenberg_kg`, exposing the identical OpenAI-style
  `/v1/images/generations` contract as the mflux one, so the worker only needs
  `IMAGE_ENDPOINT` repointed. It resolves `cuda → mps → cpu`, so unlike FLUX it
  runs on any host — fast on a GPU, usable on Apple Silicon, slow but working on
  plain CPU. `make up` selects it wherever mflux cannot run, which means image
  generation now *works* off Apple Silicon rather than being skipped.

  Two deliberate divergences from `gutenberg_kg`'s copy, both worth porting
  back. **torch is imported lazily**, behind `_torch()`, so the module can be
  imported for tests or docs from an environment without the diffusers stack —
  the same deferral `image_gen.py` already uses for mflux; the upstream copy
  imports torch at module scope and so cannot be imported outside `.venv-sdxl`.
  And **weights are fetched on first run** rather than requiring an existing
  cache: upstream hard-wires `local_files_only=True`, which fails on a fresh
  machine. `SDXL_OFFLINE=1` restores the strict behaviour once cached.
- **`make sdxl-server`** and **`make sdxl-fetch`** — the latter pre-downloads the
  ~7 GB of weights so the first `make up` is not a silent long wait. `.venv-sdxl`
  is separate from `.venv-image` because mflux and diffusers pin conflicting
  `transformers` ranges and cannot share a venv.
- **`IMAGE_BACKEND`** selects the image server: `flux` where mflux can run,
  `sdxl` everywhere else, overridable with `make up IMAGE_BACKEND=flux|sdxl`.
  Forcing `flux` on an unsupported host fails with a message naming the
  requirement and pointing at the SDXL alternative. Apple Silicon is unchanged.
- **`tests/test_sdxl_server.py` (42 tests)** covering size parsing, step
  resolution (a Lightning UNet's distilled count wins over a per-request
  override), the offline flag, and the `cuda → mps → cpu` fallback. They need no
  stubbing at all, which is the deferred-import design paying for itself.
- **`docs/DOCKER.md` (new)** — the Docker counterpart to
  `docs/APPLE_CONTAINERS.md`, which had no equivalent. Covers the pulled image
  and the from-clone path, building, generated answers via Ollama, the everyday
  targets and their raw `docker compose` equivalents, image generation, and
  troubleshooting. Docker is the default runtime and works on Linux, macOS and
  Windows; the docs had drifted into presenting the Apple-Silicon setup as the
  normal one.
- **`make build`**, the canonical build target in both runtime branches, matching
  `gutenberg_kg`. `make build-image` stays as an alias — the README, the docs and
  the changelog all reference it.
- **`make build-all`** — builds under every container runtime installed on the
  machine. Docker and Apple's `container` keep *separate image stores*, so on a
  Mac with both, an image built by one is invisible to the other and
  `make run RUNTIME=apple` after a Docker build silently has nothing to run. It
  skips a runtime that is absent rather than failing.
- **`make help` now reports what it detected** — which runtimes are installed,
  the current one, and which image backend it selected.
- **`.dockerignore` (new).** The build context is the repo root for both entry
  points (`make build-image` builds `.`, compose declares `context: ..`), and
  the image is built by `COPY .diarykg/` — so whatever `diarykg build` left in
  that directory was baked in wholesale. `gutenberg_kg` has carried this file
  for exactly this reason; here there was none. It now excludes the build-only
  artefacts that must never ship: `.diarykg/corpus/` (DiaryKG's copy of the
  source Markdown, needed by `make reindex`, never read at serve time),
  `.diarykg/snapshots/`, embedding caches, SQLite WAL/SHM sidecars, and any
  `lancedb/` directory left over from a pre-sqlite-vec build — the last of which
  is a multi-GB payload the handler cannot even open. Also drops `data/`
  (13 MB of source text), `.git/`, and the test/doc/asset trees from the context.
- **`stats` op on the worker**, returning `entries`, `chunks`, `nodes`, `edges`,
  `vectors` and `embed_model` read live from the served index. `gutenberg_kg`'s
  worker has had one; this one did not, which is why the figure below was
  hardcoded. Honours `HANDLER_SECRET` like every other op, and reports
  `{"error": ...}` rather than raising on a missing or corrupt index.
- **`make stop`.** Advertised in `make help` and declared in `.PHONY`, but the
  target itself did not exist — `make stop` failed with "No rule to make
  target". Added for both runtimes; it halts the containers while `make down`
  continues to delete them.
- **`tests/test_chat.py` (23 tests)** covering the model blocklist and the
  `stats` fetch, plus **8 tests** for the handler's `stats` op. `tests/conftest.py`
  gained a streamlit stub whose `cache_data` is an identity decorator, so the
  memoised chat helpers are testable at all. With 3 more for synthesis-failure
  degradation, the suite goes 57 → 91 tests.

### Changed
- **README quick start** now walks through `git clone` → `make pull` →
  `make up` → `make down` instead of a standalone `docker pull` +
  `docker run`. `docs/API.md` updated to match.
- **Apple `container` runtime memory defaults right-sized**: `WORKER_MEM`
  8g → 2g, `CHAT_MEM` 4g → 512m, based on `container stats` measured under
  idle and 8-way concurrent `k=50` query load (worker peaks ~1.02 GiB, chat
  ~100 MiB) rather than the previous unmeasured guess. `docs/APPLE_CONTAINERS.md`
  updated with the measurement.

- **The synthesis docs lead with Ollama rather than oMLX.** oMLX is
  Apple-Silicon-only, and `docs/API.md`, `docs/USER_GUIDE.md` and
  `docker/.env.example` all presented it as the recommended path with Ollama as a
  footnote — which is backwards for most readers. Each now opens with a
  platform-support table or the cross-platform setup, and keeps oMLX as the
  documented Apple fast path. `README.md` gained a Docker-first quick start with
  an optional-synthesis step. `docs/USER_GUIDE.md`'s link to
  `API.md#alternative-ollama` was left dangling by the restructure and now points
  at the current anchor.
- **`docker/.env.example` explains the `/v1` requirement** and gives both the
  Ollama and oMLX blocks inline, rather than shipping oMLX values with Ollama
  mentioned only in passing. `IMAGE_ENDPOINT` is now listed explicitly — compose
  reads it, but it was absent from the example file — with a note on what the
  image server needs.
- **The chat UI now filters the synthesis model list**, using the same
  `_MODEL_BLOCKLIST` as `gutenberg_kg`: reasoning models (Agents-A1, DeepSeek-R1,
  gpt-oss) whose chain-of-thought lands in the answer pane as prose, plus
  non-chat utilities (markitdown, embedding models). Both UIs read the same
  oMLX/Ollama catalogue, so a model unusable there was equally unusable here —
  but only `gutenberg_kg` was hiding them. A blocklisted model reported as the
  backend's *default* is also replaced, which is the case that used to select
  itself with no interaction at all.
- **Sidebar corpus counts come from the worker's `stats` op** instead of the
  string `"3,355 entries · 7,282 indexed chunks"` written into `chat.py`. That
  pair already disagreed with `README.md`'s own table (7,285) and would have gone
  stale on the next `make build-index`. Falls back to "corpus stats unavailable"
  when the worker is offline.
- **Container detection matches `gutenberg_kg`.** `chat.py` tested only for
  `/.dockerenv`, which Apple's `container` runtime does not create; the image now
  sets `PEPYS_IN_CONTAINER=1` and `chat.py` honours it, mirroring
  `GUTENKG_IN_CONTAINER`.
- **Version floors.** `streamlit>=1.35.0` → `>=1.59,<2`: `chat.py` calls
  `st.image(..., use_container_width=True)`, and that keyword did not exist on
  `st.image` before 1.41 — 1.35 has only the deprecated `use_column_width`, so
  any environment honouring the declared floor lost "Render response" to a
  `TypeError`. The upper bound matches `gutenberg_kg`. `watchdog>=6.0.0` added
  (declared by `gutenberg_kg`'s `[chat]` extra, missing here, so a host-side
  `make chat` fell back to polling). `pytest` floor raised to `>=9.0.3` to match
  the fleet. The Dockerfile's `streamlit httpx watchdog openai pillow` line is
  now version-bounded rather than floating to whatever was newest at build time.
- **CI lints what `make lint` lints.** `ruff check` / `ruff format --check` now
  cover `scripts/` and `tests/`, not `docker/` alone — a lint error outside
  `docker/` used to pass CI and fail locally.
- **`docker-compose.yml` gained a project `name:`** (`corpuspepys`) and `-u` on
  the worker command, matching the Dockerfile `CMD` and the Apple path so
  startup and query logs are not buffered away from `docker compose logs`.

### Removed
- **The `args:` block in `docker-compose.yml`.** It carried a second copy of
  `KGMODULE_UTILS_VERSION`, overriding the Dockerfile default at build time, so
  a compose build and `make build-image` could produce different images from the
  same tree. `gutenberg_kg` removed its copy after exactly that drift
  (kgmodule-utils 0.4.6 vs 0.5.0); the pins now live only in the Dockerfile ARGs.
  `scripts/check_pins.py` still watches compose for stray `*_VERSION` args.
- **`COPY docker/image_gen.py` from the Dockerfile.** `image_gen` is the local
  mflux/MLX path, imported only by `image_server.py`, which runs on the *host* in
  `.venv-image` because mflux needs native Apple MLX. Nothing in the image
  imports it and mflux is not installed there, so it could only ever have raised
  `ImportError`.
- **`[tool.poetry.group.dev.dependencies]`.** The same six dev tools were
  declared both there and in the PEP 621 `dev` extra — two floors to maintain,
  already drifting from the fleet. Now declared once, in the extra, as
  `gutenberg_kg` does. `commit.txt`, a gitignored scratch file, is no longer
  tracked.

### Fixed
- **README/API.md quick start never actually worked.** `docker run -p
  8000:8000 egsuchanek/corpus-pepys:latest` starts the image's default `CMD`
  (`python -u handler.py`), which runs RunPod's serverless poll loop, not an
  HTTP server — the `--rp_serve_api --rp_api_host 0.0.0.0` flags that make it
  serve `:8000` are only passed by `docker compose` (i.e. `make run`/`make up`).

- **`make up` failed on any host without Apple Silicon.** It unconditionally ran
  `make image-server`, which builds `.venv-image` from
  `docker/requirements-image.txt` and so installs mflux — and mflux needs Apple
  MLX on macOS arm64, or `mlx[cuda13]` plus an NVIDIA GPU on Linux, and ships no
  Windows wheel at all. On an ordinary x86 Docker host the pip install failed and
  took `make up` down with it, *after* the worker and chat had already started —
  so the whole stack looked broken when only an optional backend was missing.

  `make up` now detects support and skips the image server with an explanation,
  leaving search, synthesis and chat untouched; only the chat UI's "Render
  response" button is unavailable. `make image-server` called directly still
  fails, but with a readable message naming the requirement instead of a pip
  resolution error. `FORCE_IMAGE_SERVER=1` overrides the check for the CUDA 13
  Linux case, which mflux supports but the detection cannot see.
- **`docs/API.md` documented an Ollama endpoint that cannot work.** It gave
  `VLLM_ENDPOINT_URL=http://host.docker.internal:11434` with no `/v1`. The worker
  speaks the OpenAI wire protocol, so without the suffix every synthesis request
  404s while plain search keeps working — which reads as a broken feature rather
  than a bad URL. The configuration table also listed defaults without `/v1`,
  disagreeing with `docker-compose.yml` and `.env.example`, and described the
  synthesis context as coming from `DiaryKG.pack()`, which the handler stopped
  using when it moved to hydrating each hit's `content` from the index.
- **The Docker `build` target skipped the daemon check.** The Apple branch has
  always depended on `setup`; the Docker branch did not, so a stopped daemon gave
  a raw `docker build` error there and a readable one on the other path.
- **`make install` passed `--without dev` for a Poetry group that no longer
  exists** — the dev tools moved to the PEP 621 `dev` extra in the previous
  change, and extras are opt-in, so the flag now only earned a warning.
- **The chat UI could not authenticate when `HANDLER_SECRET` was set.**
  `chat.py` reads `HANDLER_SECRET` and includes it in every request, but neither
  the compose `pepys-chat` service nor the Apple `chat-container` target passed
  it in — so with a secret configured, the worker answered every chat query with
  `{"error": "unauthorized"}` while `curl` and `make query` kept working. Both
  paths now forward it. (`gutenberg_kg` has the same gap in its chat service.)
- **A failed synthesis threw away a search that had already succeeded.**
  `handler.py` called `synthesize_rag` unguarded, so an unreachable LLM server,
  an unloaded model or a timeout propagated out of the handler and failed the
  whole query — discarding hits that had already been retrieved. It now catches
  the failure and returns it as `synthesis_error` alongside the results, which
  is what `chat.py`'s "Answer generation failed" branch has always rendered:
  that branch was unreachable because nothing ever set the key. `gutenberg_kg`
  carried the identical dead path and has been fixed the same way.
- **`docs/API.md` documented a corpus scope the worker rejects.** It advertised
  `"corpus": "pepys"` in the schema, the field table, the sample response and the
  curl example; the handler accepts only `diary` and `all`, so every documented
  call failed with `unknown corpus 'pepys'`.
- **Stale and self-contradictory corpus figures.** `README.md` gave the enriched
  chunk count as both 7,282 and 7,285; `docs/BUILDING.md` described the embedding
  shape as `7,282 × 768 (all-mpnet-base-v2)` when the build stack has defaulted
  to `BAAI/bge-small-en-v1.5` (384-dim) since the sqlite-vec migration. The
  README also listed an `analysis/` directory that does not exist in this repo.
  Where the node/edge counts legitimately differ between a full build and a
  `make reindex` (which disables `SIMILAR_TO`), the docs now say so and point at
  the `stats` op for the live numbers.

---

## [0.5.1] — 2026-08-03

### Fixed
- **The chat model picker silently reverted to the provider default.** Neither
  the Provider nor the Model selectbox in `docker/chat.py` carried a `key`, so
  Streamlit derived each widget's identity from its parameters — including
  `options` and `index`. Anything that changed those made it a *new* widget and
  reset the selection: switching provider, or hitting **🔄 Refresh models**,
  which clears the cache and refetches, potentially with a different order or
  `default`.

  The reset was invisible, which is the real damage. The sidebar showed the
  default and `cfg["model"]` carried it into both the query and the image-prompt
  rewrite, so answers came back from a model you had not chosen with nothing on
  screen indicating the swap.

  Both selectboxes now use explicit keys (`synth_provider`, `synth_model`) so
  their values live in `st.session_state` and survive reruns. A reconcile step
  runs *before* the Model widget renders — Streamlit raises if `session_state`
  holds a value absent from `options`, exactly what a provider switch causes —
  so the stored choice is validated first, kept when still available and
  replaced by the provider default only when it genuinely vanished. The
  empty-models path is unchanged: `list_models` swallows failures and returns
  `([], "")`, the selectbox does not render, and `model` stays `""`
- **`APPLE_HOST_GW` fell back to the wrong vmnet gateway.** The constant was
  `192.168.65.1`, inherited from gutenberg_kg along with a comment claiming CLI
  0.1.0 used `192.168.64.0/24` and 1.1.0 moved to `192.168.65.0/24`. That is
  wrong: the `container-network-vmnet` plugin allocates `192.168.64.0/24` —
  macOS's vmnet framework default — verified on CLI 1.1.0 against a network
  created fresh by `container system start`, so it is the current allocation
  rather than a leftover. `192.168.65.x` is *Docker Desktop's* gateway subnet,
  the likely source of the number. Live detection already covered the normal
  path, so this only bit on a cold start, when the runtime is not yet running
  and the probe returns nothing — and it failed silently, with the worker unable
  to reach the LLM and answers coming back with no synthesis rather than an
  error

---

## [0.5.0] — 2026-08-03

### Added
- **Apple `container` as an alternative runtime** — `make <target> RUNTIME=apple`
  drives Apple's native `container` CLI instead of Docker (Apple Silicon,
  macOS 26), mirroring gutenberg_kg. Docker stays the default; nothing changes
  without the flag. `setup`, `build-image`, `run`, `up`, `down`, `logs` and
  `clean` each have both implementations, while `image-server`, `chat`, `query`,
  `test` and `lint` are shared because they run on the host either way. Per-VM
  sizing is exposed as `WORKER_MEM` / `WORKER_CPUS` / `CHAT_MEM` (default 8g/6
  and 4g) — each container is its own VM, so the CLI defaults are far too small
  for torch + embedder + the 41K-node graph. `docker/.env` is sourced explicitly
  to mirror compose's automatic loading. Verified end-to-end on CLI 1.1.0:
  image built, worker opened 41,486 vectors, `make query` returned scores
  identical to the Docker path, chat served on `:8501`
- **`make setup`** for both runtimes — installs the `container` CLI via Homebrew
  and runs `container system start` (idempotent, the once-per-boot step) under
  `RUNTIME=apple`; verifies the Docker daemon is reachable otherwise
- **`make logs`** — follows worker logs under either runtime
- `docs/APPLE_CONTAINERS.md` — setup, the vmnet gateway caveat, per-VM sizing,
  and disk-usage management. The Makefile header already referenced this file,
  so that pointer previously dangled

### Changed
- **`RUNPOD_LOG_LEVEL` now defaults to `INFO`** (`docker/docker-compose.yml`
  and the Apple `run` target). runpod's logger defaults to `DEBUG`, which echoes
  every handler response into the logs, and it caps a single message at 4096
  chars — replacing the middle with `...TRUNCATED N CHARACTERS...`. On a 5-hit
  query that silently drops the middle result *from the log*, which reads like
  retrieval lost results when the HTTP response was complete all along. Set
  `RUNPOD_LOG_LEVEL=DEBUG` to restore the old output

### Removed

### Fixed
- **The mflux dimension-rounding rule was documented as 32; it is 16.**
  `docker/image_gen.py`, the 0.4.0 changelog entry and `release-notes.md` all
  claimed each dimension rounds down to a multiple of 32. mflux itself warns
  "Width and height should be multiples of 16. Rounding down." The original
  claim was inferred from two observations (999→992, 333→320) that satisfy both
  rules and so could not distinguish them. Confirmed against a live server with
  a discriminating case: `1008x512` — a multiple of 16 but not 32 — round-trips
  unchanged

---

## [0.4.0] — 2026-08-03

### Added
- **`make check-pins`** (`scripts/check_pins.py`) — verifies the KG versions in
  `poetry.lock`, `docker/Dockerfile` ARGs and `docker-compose.yml` build args all
  agree, and is a prerequisite of `build-image`. The index is produced locally by
  the `[build]` extra and read by the container: since doc-kg >=0.18.2 changed the
  vector store layout, a builder older than the runtime emits an index the
  container cannot open, and it fails *silently* as empty results rather than an
  error. The pyproject floors are deliberately not checked — they express intent,
  while the lock is what `make install` resolves, so `poetry update` moving the
  lock without the Dockerfile ARGs is the drift that matters. `kg-rag` is reported
  but unchecked: it is container-only and has no lock entry
- **Test suite** (`tests/`) — 57 unit tests covering `docker/handler.py` and
  `docker/image_gen.py`, runnable with no KGRAG environment:
  - `tests/conftest.py`: stubs `runpod`, `kg_rag`, `kg_utils`, and `lancedb`
    into `sys.modules` before import so handler.py's startup code runs against
    lightweight mocks
  - `tests/test_handler.py` (36 tests): `_rows_to_hits` (score/filter/defaults),
    `_attach_diary_fields` (temp-SQLite hydration), `_semantic_search` (no-table
    guard, semantic-floor), and `handler()` dispatch (auth, query validation,
    corpus validation, response shape, synthesis on/off)
  - `tests/test_image_gen.py` (21 tests): `_parse_size` (valid, malformed,
    non-positive), `_load_model` cache hit path, and `generate()` (size
    passthrough, arbitrary sizes not snapped, every chat preset verbatim,
    malformed-size fallback, seed, output path, steps override)
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
- **The chat Resolution picker was still inert — the other half of the fix.**
  The preset was being *sent* correctly (fixed earlier), but
  `docker/image_server.py` parsed `req.size` into pixels and then discarded it,
  snapping to the nearest of seven hardcoded aspect ratios which
  `image_gen.generate()` re-expanded through `_ASPECT_SIZES`. Only `1536x1024`
  appeared in that map, so Preview (`768x512`) and Standard (`1152x768`) both
  fell through to `3:2` and rendered at full size. `_ASPECT_SIZES` is replaced by
  `image_gen._parse_size()`, `generate()` now takes `size="WIDTHxHEIGHT"` instead
  of `aspect_ratio`, and the server passes `req.size` straight through — matching
  the same fix in gutenberg_kg. Verified against a live FLUX server: all three
  presets now render at the requested dimensions, and Preview dropped from 19.1s
  to 3.2s because it is no longer doing full-size work in secret. Note the model
  rounds each dimension down to a multiple of 16 (`999x333` → 992×320); every
  preset is already a multiple of 16. `aspect_ratio` in `chat.py`'s
  `_imagine_via_worker` is untouched — that is `kg_utils.WorkerClient.imagine()`'s
  own signature, a separate path
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
