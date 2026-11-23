# Release Notes -- v0.8.0

> Released: 2026-11-23

This release gives the corpus its own logo and moves the worker's image
generation onto imagewright, the fleet's image package. Search, synthesis
and the index are unchanged.

## What changed

**An Art Techno logo.** The Great Fire of 1666, drawn as rays rising from a
die behind the City's skyline and Old St Paul's dome. `scripts/build_logo.py`
draws the mark, an icon and a banner, and the README opens with the banner.

**Image generation through imagewright.** The worker builds its image
synthesizer from imagewright instead of kgmodule-utils, and picks a
per-request image backend with the same environment variables and fallbacks
as before. kgmodule-utils will drop its copy of the image code in a later
release; this worker no longer depends on it.

**Current KG stack.** kgmodule-utils moves to 0.31.0 and kg-rag to 0.19.0, in
the lock and in the worker image.

## Upgrading

Run `make install` (or `poetry install`) to pick up imagewright and the new
pins. The published worker image needs a rebuild to include them; until then
it keeps working on its old pins. The index does not need a rebuild.

---

_Full changelog: [CHANGELOG.md](CHANGELOG.md)_
