# API Reference

For most people, the [Chat UI](USER_GUIDE.md) is the easiest way to explore the
diary. This document is for developers who want to call the service directly over
HTTP — for scripting, integration, or building their own front end.

The worker exposes the RunPod serverless API on port 8000 once it is running
(`make run` or `docker run -p 8000:8000 egsuchanek/corpus-pepys:latest`).

---

## Endpoint

`POST http://localhost:8000/runsync`

### Request

```json
{
  "input": {
    "query":          "string  — required",
    "corpus":         "diary | all  (default: all)",
    "k":              8,
    "min_score":      0.0,
    "semantic_floor": 0.0,
    "synthesize":     false
  }
}
```

| Field | Type | Default | Description |
|---|---|---|---|
| `query` | string | — | Natural-language question (required) |
| `corpus` | string | `all` | `diary` to scope to the diary, `all` for every registered KG. Any other value is rejected with `{"error": "unknown corpus ..."}` |
| `k` | int | 8 | Number of passages to return |
| `min_score` | float | 0.0 | Drop individual hits below this relevance score |
| `semantic_floor` | float | 0.0 | Discard the whole result if the best hit is below this |
| `synthesize` | bool | false | Generate a narrative answer via Ollama (see below) |
| `secret` | string | — | Required only when `HANDLER_SECRET` is set in the worker |

### Response

```json
{
  "query": "Great Fire of London",
  "corpus": "diary",
  "total_hits": 8,
  "hits": [
    {
      "node_id": "chunk:entry_4690_chunk_0.md:0002",
      "name": "...",
      "score": 0.7292,
      "summary": "...",
      "source_path": "..."
    }
  ],
  "synthesis": null
}
```

---

## Examples

Plain semantic search:

```bash
curl -s -X POST http://localhost:8000/runsync \
  -H "Content-Type: application/json" \
  -d '{"input":{"query":"Great Fire of London","corpus":"diary","k":5}}' | jq .
```

Makefile shorthand:

```bash
make query QUERY="Great Fire of London"
```

---

## Operations

Besides search, the worker accepts an `op` field instead of a `query`. These
back the chat UI's sidebar and its "Render response" button, and are the same
set gutenberg_kg's worker serves.

| `op` | Returns |
|---|---|
| `stats` | `entries`, `chunks`, `nodes`, `edges`, `vectors`, `embed_model` |
| `models` | `models` (list of ids) and `default` for the active synthesis backend |
| `rewrite` | `prompt` — a passage (`text`) rewritten into an image-generation prompt |
| `imagine` | `image_b64`, `prompt`, `aspect_ratio`, `image_model`, `image_backend` |

`HANDLER_SECRET`, when set, is required for these exactly as it is for a search.

Live index totals — the numbers in this repo's tables can only ever describe the
build they were written for, so read them from the running worker instead:

```bash
curl -s -X POST http://localhost:8000/runsync \
  -H "Content-Type: application/json" \
  -d '{"input":{"op":"stats"}}' | jq .
```

```json
{
  "entries": 3355,
  "chunks": 7282,
  "nodes": 41517,
  "edges": 333679,
  "vectors": 7282,
  "embed_model": "BAAI/bge-small-en-v1.5"
}
```

---

## LLM synthesis

Set `"synthesize": true` to get a generated answer grounded in the retrieved
passages instead of just the ranked hit list. This requires a local LLM server
exposing an OpenAI-compatible `/v1/chat/completions` endpoint.

### Recommended: oMLX (Apple Silicon)

[oMLX](https://omlx.ai) is a fast, multi-model, OpenAI-compatible server for
Apple Silicon. The worker runs on port 8000, so start oMLX on **8080**:

```bash
make serve-llm                 # starts oMLX on http://localhost:8080
```

Point the worker at it:

```bash
cp docker/.env.example docker/.env
# defaults already target oMLX on host.docker.internal:8080
# set VLLM_API_KEY to your oMLX key (~/.omlx/settings.json → auth.api_key)
make run
```

Then query with synthesis enabled:

```bash
curl -s -X POST http://localhost:8000/runsync \
  -H "Content-Type: application/json" \
  -d '{"input":{"query":"What did Pepys think of the navy?","synthesize":true,"k":6}}' | jq .
```

The synthesised answer is built from the full text of the retrieved diary
entries (via `DiaryKG.pack()`), not the truncated summaries — so it quotes
real passages with their dates. Any `<think>…</think>` reasoning blocks from
the model are stripped before the answer is returned.

### Alternative: Ollama

Cross-platform. Set in `docker/.env`:

```bash
VLLM_ENDPOINT_URL=http://host.docker.internal:11434
VLLM_MODEL=qwen3:4b
VLLM_API_KEY=
```

### Configuration

| Variable | Default | Description |
|---|---|---|
| `VLLM_ENDPOINT_URL` | `http://host.docker.internal:8080` | OpenAI-compatible base URL (oMLX `:8080`, Ollama `:11434`) |
| `VLLM_MODEL` | `Qwen3-4B-Instruct-2507-MLX-8bit` | Model ID used for synthesis |
| `VLLM_API_KEY` | _(empty)_ | Bearer token for the endpoint (your oMLX key; leave empty for Ollama) |
| `HANDLER_SECRET` | _(unset)_ | Optional shared secret; when set, requests must include `"secret"` |
| `EMBED_MODEL` | `BAAI/bge-small-en-v1.5` | Sentence-transformer model for query embedding |
