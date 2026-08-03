# Release Notes — v0.4.0

> Released: 2026-08-03

This release fixes a worker that would not start, an index build that produced a
container-unreadable index, and a resolution picker that had never actually
changed anything. Underneath all three was the same cause: versions arriving
from somewhere nobody had pinned. The Docker image no longer inherits a shared
base, dropping from 10.9 GB to 3.6 GB along the way.

## What changed

**The image stands on its own.** It previously extended a fleet-wide
`kgrag-worker` base that was corpus-agnostic and bulk-installed every corpus
package, so this image carried Gutenberg and metabolomics code it never
imported, a LanceDB stack it never read, and — critically — an unpinned `kg-rag`
that shipped two years of API behind the handler. The worker crash-looped at
startup on a `KGEntry` field that version had never heard of, while the chat
container stayed up and made the stack look healthy. The image is now built from
`python:3.12-slim` with every runtime package pinned explicitly. Installing
CPU-only PyTorch before the KG stack removes 2.9 GB of NVIDIA runtime and 660 MB
of Triton that a GPU-less container could never use.

**The index and the container now agree.** `make build-index` was invoking a
globally installed `diarykg` — three packages behind what the container expected.
Because doc-kg 0.18.2 changed the vector store layout, that combination silently
produced an index the runtime could not open, surfacing as empty search results
rather than an error. The build toolchain now lives in the project virtualenv,
pinned by a new `[build]` extra, and `make check-pins` refuses to build an image
whose runtime versions disagree with the lockfile that produced the index.

**Image resolution works.** Choosing Preview or Standard in the chat app had no
effect: the requested pixel size was parsed and then discarded in favour of the
nearest of seven hardcoded aspect ratios, all but one of which mapped back to
full size. Sizes now pass through untouched. Preview renders roughly six times
faster as a result, since it is no longer quietly doing full-resolution work.

**Setup is one command.** `make install` prepares a working environment —
runtime, build toolchain and the spaCy model that `build-index` requires and
which cannot be declared as an ordinary dependency. `make install-dev` adds the
test and lint tooling.

## Upgrading

Rebuild both artifacts, in order: `make install`, then `make build-index`, then
`make build-image`. The index must be rebuilt before the image — `check-pins`
will stop you if the versions have drifted, but it cannot tell whether the baked
index is current.

If you consume `image_gen.generate()` directly, it now takes `size="WIDTHxHEIGHT"`
in place of `aspect_ratio`. Any dimensions are accepted rather than seven fixed
ratios; the model rounds each down to a multiple of 32.

---

_Full changelog: [CHANGELOG.md](CHANGELOG.md)_
