# Release Notes — v0.6.0

> Released: 2026-09-18

Image generation now works off Apple Silicon, the Docker quick start actually works, and three silent failures are fixed.

## What changed

**Image generation runs on any host.** A new SDXL-Lightning image server, ported from `gutenberg_kg`, joins the existing mflux backend: `make up` picks SDXL wherever mflux can't run — fast on a GPU, usable on Apple Silicon, working (if slow) on plain CPU. Previously, image generation was silently skipped everywhere but Apple Silicon.

**The Docker path got a full pass.** `make pull` retags the published image so a bare `docker pull` no longer leaves it invisible to `make run`. `docs/DOCKER.md` is new. `make up` no longer takes the whole stack down when it can't build the (optional) image-server dependency on a non-Apple host — search, synthesis and chat all stay up. Apple's `container` runtime memory limits are now based on measured usage rather than guesses, four times smaller than before.

**Three bugs were silently discarding real work.** A failed synthesis call used to fail the whole query, discarding search results that had already succeeded — it now returns the results with a `synthesis_error` alongside them. Chat authentication silently rejected every request once `HANDLER_SECRET` was configured, because neither container target forwarded it. And the README's own quick-start command never worked at all: it started the image's default serverless mode instead of the HTTP server `docker compose` actually configures.

**Documentation caught up to the code.** The corpus's chunk count was quoted as two different numbers in two different places; the embedding model description was two migrations out of date; API docs described endpoints and parameters the handler no longer accepts.

## Upgrading

`docker pull egsuchanek/corpus-pepys:latest && make pull` for the published image, or rebuild from source. No config changes required; `IMAGE_BACKEND` auto-selects SDXL where mflux isn't available.

---

_Full changelog: [CHANGELOG.md](CHANGELOG.md)_
