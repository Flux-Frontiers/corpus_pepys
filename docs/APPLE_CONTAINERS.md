# Apple `container` as a Docker alternative for corpus_pepys

*Status: implemented as an alternative runtime — `make <target> RUNTIME=apple` —
and verified end-to-end on Apple Silicon / macOS 26 with `container` CLI 1.1.0
(2026-08-03). Mirrors the same arrangement in
[gutenberg_kg](https://github.com/Flux-Frontiers/gutenberg_kg).*

Docker remains the default, and is what most people should use — see
[DOCKER.md](DOCKER.md). Nothing on this page applies unless you pass
`RUNTIME=apple`.

## Using it

Requirements: Apple Silicon, macOS 26 (Tahoe), and Apple's
[`container`](https://github.com/apple/container) tool v1.1+
(`brew install container`, or the pkg from GitHub releases).

```sh
make setup       RUNTIME=apple   # installs the CLI if missing + `container
                                 # system start`; build/run depend on it,
                                 # so calling it directly is optional
make build       RUNTIME=apple   # container build -f docker/Dockerfile
make run         RUNTIME=apple   # worker on http://localhost:8000
make up          RUNTIME=apple   # worker + chat UI + FLUX image server
make logs        RUNTIME=apple   # container logs -f pepys-worker
make down        RUNTIME=apple   # delete both containers + image server
make clean       RUNTIME=apple   # remove the index and the image
```

`make query`, `make chat`, `make test` and `make lint` are runtime-independent —
they run on the host and talk to `localhost`, so they work unchanged either way.

## What to know

- **Memory and CPU are per-container VM flags.** Each container is its own VM,
  so memory is a hard upper bound rather than a share of one big Docker Desktop
  VM, and the CLI defaults are far too small for the worker (torch + embedder +
  41K-node graph + 41K vectors). The Make targets pass `8g`/6 CPUs for the
  worker and `4g` for chat. Override per-invocation:
  `make run RUNTIME=apple WORKER_MEM=12g`. Allocation is lazy, so `8g` does not
  pin 8 GB of RAM.

- **Ports are published to the host.** `container` gained Docker-style
  `--publish` in CLI v1.1.0, so `8000` and `8501` reach `localhost` exactly as
  they do under compose.

- **`docker/.env` still works.** The Apple targets source it explicitly before
  `container run`, mirroring compose's automatic `.env` loading.

- **Host services are reached at the vmnet gateway, not `host.docker.internal`.**
  That hostname does *not* resolve inside these VMs. Anything pointing at the
  host — the oMLX/Ollama LLM, the FLUX image server, chat→worker — is rewritten
  to `APPLE_HOST_GW` before launch. Without that rewrite a `.env` aimed at
  `host.docker.internal` silently disables synthesis: the worker cannot resolve
  the name, so you get answers with no LLM rather than an error.

- **The gateway subnet is detected, not hardcoded.** It is not stable across CLI
  versions, and getting it wrong fails silently. The Makefile reads it from the
  live network and only falls back to a constant when the runtime is not yet
  started:

  ```make
  APPLE_HOST_GW ?= $(or $(shell container network inspect default 2>/dev/null \
      | sed -n 's/.*"ipv4Gateway" : "\([0-9.]*\)".*/\1/p' | head -1),192.168.65.1)
  ```

  This is worth the trouble. On the machine this was developed on — CLI 1.1.0,
  which is documented as using `192.168.65.0/24` — the live gateway was actually
  **`192.168.64.1`**. A hardcoded default would have broken synthesis and image
  generation with no visible error. Override explicitly if needed:
  `make up RUNTIME=apple APPLE_HOST_GW=192.168.64.1`.

- **Host services must bind `0.0.0.0`, not `127.0.0.1`.** A loopback-only
  listener refuses vmnet connections. `make image-server` already binds
  `0.0.0.0`; start oMLX with `--host 0.0.0.0` likewise.

- **The FLUX image server always runs on the host, under both runtimes.** mflux
  needs native MLX and cannot run inside a Linux VM. `make up RUNTIME=apple`
  starts it on the host and points the containers at it over the vmnet.

- **`make run` is idempotent.** A running worker is left alone — it takes a
  while to load the index and embedder — while a stopped or stale container is
  replaced.

- **No restart policy.** Unlike compose's `restart: unless-stopped`, the worker
  stays down after a reboot until you `make run RUNTIME=apple` again.

- **`make chat` is unchanged** and still runs Streamlit on the host against
  `localhost:8000`. The containerised chat UI comes up as part of
  `make up RUNTIME=apple`, as the `pepys-chat` container.

## Verified

Built and run under `container` 1.1.0 on 2026-08-03:

- `container build` produced the image from the same `docker/Dockerfile`
- worker started, registered the corpus and opened **41,486 vectors** from
  `vectors.sqlite`
- `make query` returned identical scores and timestamps to the Docker path
  (0.7292 / 0.7176 / 0.7061 …), confirming the same index and retrieval
- chat UI served on `:8501`, FLUX image server on `:8090`

## Disk usage

Each image keeps a snapshot under
`~/Library/Application Support/com.apple.container/snapshots/`, and stopped
containers retain their own writable layer under `containers/`. These are not
reclaimed automatically and can grow to hundreds of GB across projects.

```sh
container system df                  # images / containers / reclaimable
container image list
container image rm <image>           # frees the image AND its snapshot
container image prune                # dangling only — often reclaims nothing
container image prune --all          # every image not referenced by a container
container delete <name>              # reclaims a stopped container's layer
```

`prune --all` counts a *stopped* container's image as unused, so it will happily
remove images you still want. Prefer explicit `container image rm`.

The corpus_pepys image is ~2.7 GB as a snapshot — the CPU-only torch install in
`docker/Dockerfile` is what keeps it there rather than ~7 GB.

## Not covered

The RunPod deployment path is Docker-only and untouched by this. `make
build-image RUNTIME=apple` produces a local image; publishing to Docker Hub
still goes through Docker.
