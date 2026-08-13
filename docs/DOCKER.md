# Running corpus_pepys with Docker

Docker is the **default runtime** and the one to use unless you are on Apple
Silicon and specifically want Apple's native `container` CLI (see
[APPLE_CONTAINERS.md](APPLE_CONTAINERS.md)). Everything here works on Linux,
macOS (Intel or Apple Silicon) and Windows.

Nothing on this page needs Apple hardware. Where a feature does, it is called
out — there is exactly one, and it is optional.

---

## Requirements

- Docker Engine or Docker Desktop, with the daemon running.
- ~4 GB of free disk for the image (the diary index is baked in).
- Optionally [Ollama](https://ollama.com) for generated answers.

`make setup` verifies the daemon is reachable and tells you what is wrong if not.
Every container target depends on it, so you rarely call it directly.

---

## Quick start

### Option A — pull the published image

Nothing to build:

```bash
docker pull egsuchanek/corpus-pepys:latest
docker run -p 8000:8000 egsuchanek/corpus-pepys:latest
```

The worker comes up on `http://localhost:8000` with the index already inside it.
No volumes, no model download.

### Option B — from a clone

```bash
make run          # worker on http://localhost:8000
make up           # worker + chat UI on http://localhost:8501
```

`make run` and `make up` drive `docker compose`, which pulls or builds as needed.

Check it:

```bash
make query QUERY="Great Fire of London"
```

---

## Building the image yourself

Only needed if you changed the corpus, the handler, or the pins.

```bash
make build-index   # produces .diarykg/ locally  (~3 min)
make build         # docker build, bakes .diarykg/ into corpus-pepys:latest
```

`make build` runs `make check-pins` first, which verifies the KG package
versions in `poetry.lock` and `docker/Dockerfile` agree. They must: the index is
written by the local toolchain and read by the container, and a mismatch fails
*silently*, as empty query results rather than an error.

`make build-image` is an alias for `make build`, kept for older docs and muscle
memory.

### Building for both runtimes

On a Mac carrying both Docker Desktop and Apple's `container` CLI, the two keep
separate image stores — an image built by one is invisible to the other, so
`make run RUNTIME=apple` after a Docker build silently has nothing to run.

```bash
make build-all     # builds under every runtime installed on this machine
```

It skips a runtime that is not installed rather than failing, so on Linux it
simply builds with Docker and says it skipped the other.

---

## Generated answers (optional)

Search works with no LLM at all. To get a narrative answer instead of just
ranked passages, point the worker at an OpenAI-compatible server.

**Ollama is the cross-platform choice** and needs no API key:

```bash
ollama pull qwen3:4b
cp docker/.env.example docker/.env
```

Then in `docker/.env`:

```bash
VLLM_ENDPOINT_URL=http://host.docker.internal:11434/v1
VLLM_MODEL=qwen3:4b
VLLM_API_KEY=
```

Restart the worker (`make down && make up`) and turn on **Generate answer** in
the chat sidebar, or pass `"synthesize": true` to the API.

The `/v1` suffix matters — see the
[API Reference](API.md#ollama-cross-platform--start-here) for why, and for the
oMLX and OpenAI alternatives.

> `host.docker.internal` resolves on Linux because
> `docker/docker-compose.yml` declares
> `extra_hosts: ["host.docker.internal:host-gateway"]`. If you run the container
> by hand instead of through compose, add that flag or use the host's real IP.

---

## What needs Apple Silicon

One thing: the **local FLUX image server** behind the chat UI's *Render
response* button. `make image-server` builds an isolated `.venv-image` from
`docker/requirements-image.txt`, which installs [mflux] — and mflux needs Apple
MLX on macOS arm64, or `mlx[cuda13]` and an NVIDIA GPU on Linux. It ships no
Windows wheel.

`make up` detects this and skips the image server with a note rather than
failing, so the worker and chat UI come up normally. Everything except that one
button is unaffected: search, synthesis, the API, and the whole chat flow.

If you are on a CUDA 13 Linux box, mflux does support you — the detection just
does not know it. Force it:

```bash
make image-server FORCE_IMAGE_SERVER=1
```

You can also point the worker at any OpenAI-compatible image endpoint via
`IMAGE_ENDPOINT`, or select the *OpenAI* provider in the chat sidebar to
generate images through DALL·E instead.

[mflux]: https://github.com/filipstrand/mflux

---

## Everyday targets

```bash
make run        # worker only
make up         # worker + chat UI (+ image server where supported)
make chat       # Streamlit UI on the host, against a running worker
make stop       # halt the containers, keep them
make down       # stop and remove the containers
make logs       # follow worker logs
make clean      # remove the index and the image
make query      # smoke-test curl  (QUERY="..." to override)
```

`make help` prints the same list plus what it detected on your machine — which
runtimes are installed, and whether the image server can run here.

Prefer raw compose? The Make targets are thin wrappers:

```bash
docker compose -f docker/docker-compose.yml up -d              # worker
docker compose -f docker/docker-compose.yml --profile chat up  # + chat UI
```

---

## Troubleshooting

**"Docker daemon not running"** — `make setup` checks this before every build or
run. Start Docker Desktop, or `sudo systemctl start docker` on Linux.

**Chat says it cannot connect to the worker.** The worker takes a little while
on first start: it loads torch, the embedder and the index. `make logs` until
you see `[startup] ready`.

**Answers come back with no synthesis and no error.** The worker could not reach
your LLM server. Check `VLLM_ENDPOINT_URL` includes `/v1`, and that the server is
listening on `0.0.0.0` rather than `127.0.0.1` — the container cannot reach a
loopback-only listener on the host.

**Every chat query says "unauthorized".** `HANDLER_SECRET` is set for the worker
but not for the chat container. Compose passes it to both from `docker/.env`; if
you started them separately, set it in both.

**Empty results after rebuilding the index.** The index builder and the
container disagree on version. Run `make check-pins`.
