# Release Notes -- v0.7.0

> Released: 2026-10-20

### Added

- **`scripts/enrich_full_corpus.py`** runs the Phase 3 topic classifier over
  every chunk in `pepys_clean_chunks.pkl` and writes a pipe-delimited enriched
  corpus for embedding experiments; `scripts/ENRICHED_CORPUS.md` documents it.

### Changed

- **Fleet dependency floors raised and relocked** (`kgrag_priv` sweep item 46):
  `kgmodule-utils` to `>=0.23.0`, `doc-kg` to `>=0.27.0`. The three packages released on 2026-09-20 and put
  every consumer's lock behind them within hours; this is the routine
  currency bump that follows.

- **Docker image pins moved to the lock's versions** (`kgrag_priv` sweep
  item 57). The floor raise above left `docker/Dockerfile` at
  `kgmodule-utils==0.22.0` and `doc-kg==0.26.0` against a lock at 0.23.0 and
  0.27.0, so `scripts/check_pins.py` failed: an index built by the lock would
  have been read by older versions in the container. `check_pins.py --bump`
  now moves the set to the latest releases: `kgmodule-utils` 0.24.0 (floor
  and ARG), `diary-kg` 0.100.0 (floor and ARG), `doc-kg` 0.27.0 (ARG) and
  `kg-rag` 0.17.0 (container-only ARG), relocked to match.

- **`ruff` floor raised from `>=0.4.0` to `>=0.15`**, inside the existing
  `<0.16` cap (`kgrag_priv` sweep item 49, tier 1). Every fleet lock already
  installs 0.15, so the old floor meant nothing.

- **Dependencies refreshed to clear 36 Dependabot alerts**, among them a
  critical GitPython advisory: gitpython 3.2.1, pyjwt 2.15.1, urllib3 2.8.0,
  mcp 1.30.0 and virtualenv 21.14.11 (dev), all transitive.
- **`.mcp.json` is portable and tracked** (#21, sweep item 50): `${HOME}` and
  repo-relative paths in place of absolute `/Users/...` paths.
- **CI actions moved off Node 20** to checkout@v5, setup-python@v6, cache@v5,
  upload-artifact@v7 and download-artifact@v8.

### Fixed

- **PDF ingest on a fresh install, and the image pins moved with it.**
  kgmodule-utils 0.27.0's `[pdf]` resolved to the empty pymupdf4llm 2.0
  placeholder, leaving no PDF code (kgrag_priv sweep item 86). The KG pin
  set moves to kgmodule-utils 0.27.1, doc-kg 0.28.1 and kg-rag 0.17.1 in the
  pyproject floors and `docker/Dockerfile` ARGs, via
  `scripts/check_pins.py --bump`; the lock takes pymupdf 2.0. The image
  needs a rebuild to pick this up.

- **Zenodo concept DOI corrected** in `README.md` and `CITATION.cff`:
  `10.5281/zenodo.20531928`, not v0.1.1's version DOI `...20531929`.

---

_Full changelog: [CHANGELOG.md](CHANGELOG.md)_
