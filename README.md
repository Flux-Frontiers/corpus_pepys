[![Python](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue.svg)](https://www.python.org/)
[![License: Elastic-2.0](https://img.shields.io/badge/License-Elastic%202.0-blue.svg)](https://www.elastic.co/licensing/elastic-license)
[![Version](https://img.shields.io/badge/version-0.1.0-blue.svg)](https://github.com/Flux-Frontiers/corpus_pepys/releases)
[![Docker](https://img.shields.io/docker/v/egsuchanek/corpus-pepys?label=Docker%20Hub&color=2496ED)](https://hub.docker.com/r/egsuchanek/corpus-pepys)
[![Poetry](https://img.shields.io/endpoint?url=https://python-poetry.org/badge/v0.json)](https://python-poetry.org/)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.XXXXXXX.svg)](https://doi.org/10.5281/zenodo.XXXXXXX)

# corpus_pepys

**The Diary of Samuel Pepys — Interactive Semantic Knowledge Graph**

*Eric G. Suchanek, PhD · Flux-Frontiers, Liberty TWP, OH*

<p align="center">
  <img src="assets/pepys_writing.jpg" alt="Samuel Pepys writing by candlelight, the Great Fire of London visible through the window" width="720"/>
</p>

---

A standalone, queryable knowledge graph of Samuel Pepys' complete diary (1660–1669),
built with [DiaryKG](https://github.com/Flux-Frontiers/diary_kg) and served via the
[KGRAG](https://github.com/Flux-Frontiers/kgrag) federated query layer.

Query 9 years of 17th-century London history — the Great Plague, the Great Fire,
the Restoration court — with natural language, locally, in under a second.
No index to build. No model to download. Just pull and run.

---

## Quick start

### 1. Pull and run

```bash
docker pull egsuchanek/corpus-pepys:latest
docker run -p 8000:8000 egsuchanek/corpus-pepys:latest
```

Or with the repo cloned:

```bash
make run
```

The KGRAG worker starts on `http://localhost:8000`. The index is baked into
the image — no volumes, no setup.

### 2. Query it

```bash
curl -s -X POST http://localhost:8000/runsync \
  -H "Content-Type: application/json" \
  -d '{"input":{"query":"Great Fire of London","corpus":"pepys","k":5}}' | jq .
```

Or with the Makefile shorthand:

```bash
make query QUERY="Great Fire of London"
```

### 3. Chat UI

```bash
make chat
```

Opens the Streamlit chat interface at `http://localhost:8501`. The worker
must be running first (`make run`).

---

## API reference

**Endpoint:** `POST http://localhost:8000/runsync`

```json
{
  "input": {
    "query":          "string  — required",
    "corpus":         "pepys | all  (default: all)",
    "k":              8,
    "min_score":      0.0,
    "semantic_floor": 0.0,
    "synthesize":     false
  }
}
```

**Response:**

```json
{
  "query": "Great Fire of London",
  "corpus": "pepys",
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

## LLM synthesis via Ollama

Set `"synthesize": true` to get a generated answer grounded in the retrieved
passages. Requires a running [Ollama](https://ollama.com) instance on the host.

```bash
cp docker/.env.example docker/.env
# edit VLLM_ENDPOINT_URL and VLLM_MODEL (default: qwen3:4b)
make run
```

Then:

```bash
curl -s -X POST http://localhost:8000/runsync \
  -H "Content-Type: application/json" \
  -d '{"input":{"query":"What did Pepys think of the navy?","synthesize":true,"k":6}}' | jq .
```

---

## What's in this repo

| Path | Contents |
|---|---|
| `data/pepys_clean.txt` | 3,355 parsed diary entries, one per day (1660-01-01 → 1669-08-02) |
| `data/pepys_enriched_full.txt` | 7,282 semantically enriched, topic-classified chunks |
| `config/` | Topic classification YAML — 30+ Pepys-specific categories |
| `docker/` | Dockerfile, handler, docker-compose, and Streamlit chat UI |
| `docs/` | Technical articles and build instructions |
| `analysis/` | Run summaries with corpus statistics |
| `scripts/` | Source processing scripts (parse, classify, analyse) |

---

## The corpus

Samuel Pepys kept a diary from January 1660 to May 1669 — nearly a decade of daily
entries covering the most dramatic years of Restoration England: the return of Charles II,
the Great Plague of 1665, the Great Fire of London of 1666, and the day-to-day machinery
of the Royal Navy.

| Metric | Value |
|---|---|
| Total entries | 3,355 |
| Enriched chunks | 7,282 |
| Time span | 1660-01-01 → 1669-08-02 |
| KG nodes | 41,738 |
| KG edges | 564,311 |

---

## Technical background

See the [docs/](docs/) directory for full technical write-ups:

- **[Building from Source](docs/BUILDING.md)** — rebuilding the index, Docker
  image, or enriched corpus from scratch.
- **[DiaryKG](https://github.com/Flux-Frontiers/diary_kg)** — the underlying knowledge
  graph engine: architecture, NLP pipeline, and retrieval benchmarks.

---

## Citation

If you use this corpus or dataset in your research, please cite:

```bibtex
@software{suchanek_corpus_pepys_2026,
  author    = {Suchanek, Eric G.},
  title     = {corpus\_pepys: The Diary of Samuel Pepys —
               Interactive Semantic Knowledge Graph},
  year      = {2026},
  publisher = {Zenodo},
  version   = {v0.1.0},
  doi       = {10.5281/zenodo.XXXXXXX},
  url       = {https://doi.org/10.5281/zenodo.XXXXXXX}
}
```

> **Note:** Replace `XXXXXXX` with the actual Zenodo record ID once deposited.

---

## License

The diary text is in the public domain (Project Gutenberg).
All code and documentation in this repository is licensed under the
[Elastic License 2.0](https://www.elastic.co/licensing/elastic-license).

© 2026 Eric G. Suchanek, PhD — Flux-Frontiers
