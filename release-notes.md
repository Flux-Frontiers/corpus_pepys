# Release Notes -- v0.7.1

> Released: 2026-10-20

### Changed

- **KG pin set moved to diary-kg 0.100.1 and kg-rag 0.17.2** by
  `scripts/check_pins.py --bump`: the pyproject floor, `poetry.lock` and the
  `docker/Dockerfile` ARGs. Both releases carry the pymupdf 2.0 PDF fix;
  neither changes the index, so `.diarykg/` stays valid.

---

_Full changelog: [CHANGELOG.md](CHANGELOG.md)_
