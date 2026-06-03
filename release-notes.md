# Release Notes — v0.1.1

> Released: 2026-06-03

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

_Full changelog: [CHANGELOG.md](CHANGELOG.md)_
