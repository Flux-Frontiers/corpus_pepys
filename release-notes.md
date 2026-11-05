# Release Notes -- v0.7.2

> Released: 2026-11-04

This release moves the corpus onto the current fleet stack and rebuilds the
Pepys index on it. Search results are unchanged; chunk text now keeps the
diary's own line breaks, and the published image runs on both x86 and
Apple Silicon.

## What changed

**Current KG stack.** The pins move as a set to kgmodule-utils 0.29.2,
doc-kg 0.30.0, diary-kg 0.101.0 and, in the container, kg-rag 0.18.1. Every
MCP server is now on the shared `kg_utils.mcp` base. Pillow is floored at
13.0.0, which clears the Dependabot advisories against 12.3.0.

**Index rebuilt.** doc-kg 0.30.0 stores each chunk as the source text it
spans, so 587 of 18,167 chunks get their paragraph breaks back. Chunk ids
and node counts are unchanged, and standard queries return the same
results at the same scores. The rebuilt index carries no `SIMILAR_TO`
edges, as `make reindex` intends, so the graph has 253,352 edges instead
of 333,679.

**Real-index tests.** The handler and chat tests run against a small
Pepys-shaped index written with kg_utils' real sqlite-vec backend instead
of stubs, and a contract test checks that layout against any built
`.diarykg`.

**Image publishing.** `make publish-image` builds for `linux/amd64` and
`linux/arm64` and deletes the image it replaced on Docker Hub, given a
Docker Hub token with Delete.

## Upgrading

Pull the new image, or run `make reindex` on an existing checkout after
`poetry install` to rebuild the index on doc-kg 0.30.0. Nothing in the query
API changes.

---

_Full changelog: [CHANGELOG.md](CHANGELOG.md)_
