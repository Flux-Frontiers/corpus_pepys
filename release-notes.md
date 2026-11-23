# Release Notes -- v0.8.1

> Released: 2026-11-23

This is a security release. corpus_pepys now requires imagewright 0.2.0, the release that stops printing the OpenAI API key. Search, synthesis, image generation and the index are unchanged.

## What changed

**imagewright 0.2.0 is the floor.** In imagewright 0.1.0, the repr of a resolved `ImageConfig` included `api_key`, so printing or logging the config exposed the OpenAI key. 0.2.0 keeps the key out of the repr. The floor moves in `pyproject.toml` and in the worker image's `docker/Dockerfile`, so a fresh install and a fresh image both get the fixed release.

## Upgrading

Run `poetry install` to pick up imagewright 0.2.0. Rebuild the worker image (`make build`, or `make publish-image` for Docker Hub) so the container carries it too. If an OpenAI key was printed or logged while you ran 0.1.0, rotate it.

---

_Full changelog: [CHANGELOG.md](CHANGELOG.md)_
