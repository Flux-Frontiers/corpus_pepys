[![CI](https://github.com/Flux-Frontiers/corpus_pepys/actions/workflows/ci.yml/badge.svg)](https://github.com/Flux-Frontiers/corpus_pepys/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue.svg)](https://www.python.org/)
[![License: Elastic-2.0](https://img.shields.io/badge/License-Elastic%202.0-blue.svg)](https://www.elastic.co/licensing/elastic-license)
[![Texts](https://img.shields.io/badge/texts-Public%20Domain-green.svg)](https://www.gutenberg.org/)
[![Version](https://img.shields.io/badge/version-0.5.0-blue.svg)](https://github.com/Flux-Frontiers/corpus_pepys/releases)
[![Entries](https://img.shields.io/badge/entries-3%2C355-orange.svg)](#the-corpus)
[![Nodes](https://img.shields.io/badge/nodes-41.5K-green.svg)](#the-corpus)
[![Edges](https://img.shields.io/badge/edges-334K-green.svg)](#the-corpus)
[![Docker](https://img.shields.io/docker/v/egsuchanek/corpus-pepys?label=Docker%20Hub&color=2496ED)](https://hub.docker.com/r/egsuchanek/corpus-pepys)
[![Poetry](https://img.shields.io/endpoint?url=https://python-poetry.org/badge/v0.json)](https://python-poetry.org/)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.20531929.svg)](https://doi.org/10.5281/zenodo.20531929)

# corpus_pepys

**The Diary of Samuel Pepys — Interactive Semantic Knowledge Graph**

*Eric G. Suchanek, PhD · Flux-Frontiers, Liberty TWP, OH*

<p align="center">
  <img src="assets/pepys_writing.jpg" alt="Samuel Pepys writing by candlelight, the Great Fire of London visible through the window" width="720"/>
</p>

---

## Who was Samuel Pepys?

Samuel Pepys (1633–1703) was a London administrator who rose from tailor's son to
Chief Secretary of the Admiralty, reforming the Royal Navy along the way. But he is
remembered for something he never meant to publish: a private diary kept in
shorthand cipher from 1660 to 1669, decoded only in the 19th century.

For nine years Pepys wrote down everything — affairs of state and affairs of the
heart, the price of a periwig and the terror of plague carts, quarrels with his
wife, nights at the theatre, the taste of his first cup of tea. He recorded the
Restoration of Charles II, fled the Great Plague of 1665, and watched the Great Fire
of 1666 consume his city from a boat on the Thames, burying his wine and his
parmesan cheese in the garden as the flames drew near.

His unflinching candour — vain, curious, ambitious, and entirely human — makes him
one of the greatest diarists in the English language, and his diary the single most
vivid first-hand window into Restoration London.

---

This repository turns that diary into a **standalone, queryable knowledge graph**,
built with [DiaryKG](https://github.com/Flux-Frontiers/diary_kg) and served via the
[KGRAG](https://github.com/Flux-Frontiers/kgrag) federated query layer.

Query nine years of 17th-century London — the Great Plague, the Great Fire, the
Restoration court — with natural language, locally, in under a second.
No index to build. No model to download. Just pull and run.

---

## Quick start

### 1. Pull and run

```bash
docker pull egsuchanek/corpus-pepys:latest
docker run -p 8000:8000 egsuchanek/corpus-pepys:latest
```

Or, with the repo cloned, simply:

```bash
make run
```

The worker starts on `http://localhost:8000`. The diary index is baked into the
image — no volumes, no model download, no setup.

### 2. Open the chat app

```bash
make chat
```

This opens the **Pepys chat app** in your browser at `http://localhost:8501` — the
easiest way to explore the diary. Ask questions in plain English and read the entries
that answer them; no command line required.

→ **[Read the User Guide](docs/USER_GUIDE.md)** for a full walkthrough.

<p align="center">
  <em>Prefer to script it? The service also speaks HTTP — see the
  <a href="docs/API.md">API Reference</a>.</em>
</p>

---

## What's in this repo

| Path | Contents |
|---|---|
| `data/pepys_clean.txt` | 3,355 parsed diary entries, one per day (1660-01-01 → 1669-08-02) |
| `data/pepys_enriched_full.txt` | 7,282 semantically enriched, topic-classified chunks |
| `config/` | Topic classification YAML — 30+ Pepys-specific categories |
| `docker/` | Dockerfile, handler, docker-compose, and Streamlit chat UI |
| `docs/` | User guide, API reference, and build instructions |
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
| Enriched chunks | 7,285 |
| Time span | 1660-01-01 → 1669-08-02 |
| KG nodes | 41,517 |
| KG edges | 333,679 |

---

## Documentation

- **[User Guide](docs/USER_GUIDE.md)** — how to explore the diary with the chat app.
- **[API Reference](docs/API.md)** — HTTP endpoint, parameters, and LLM synthesis
  for scripting and integration.
- **[Building from Source](docs/BUILDING.md)** — rebuilding the index, Docker
  image, or enriched corpus from scratch.
- **[DiaryKG](https://github.com/Flux-Frontiers/diary_kg)** — the underlying knowledge
  graph engine: architecture, NLP pipeline, and retrieval benchmarks.

---

## Citation

If you use this corpus or dataset in your research, use GitHub's **Cite this
repository** button or [`CITATION.cff`](CITATION.cff):

```bibtex
@software{suchanek_corpus_pepys_2026,
  author    = {Suchanek, Eric G.},
  title     = {corpus\_pepys: The Diary of Samuel Pepys —
               Interactive Semantic Knowledge Graph},
  year      = {2026},
  publisher = {Zenodo},
  version   = {v0.5.0},
  doi       = {10.5281/zenodo.20531929},
  url       = {https://doi.org/10.5281/zenodo.20531929}
}
```

---

## License

The diary text is in the public domain (Project Gutenberg).
All code and documentation in this repository is licensed under the
[Elastic License 2.0](https://www.elastic.co/licensing/elastic-license).

© 2026 Eric G. Suchanek, PhD — Flux-Frontiers
