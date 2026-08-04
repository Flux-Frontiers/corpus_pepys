# Release Notes — v0.5.1

> Released: 2026-08-03

Two fixes for bugs that shared a failure mode: both were silent. Nothing
crashed, nothing appeared in a log, and the interface kept showing what you
expected while something else happened underneath.

## What changed

**The chat model picker kept its selection.** Choosing a synthesis model and
then hitting **Refresh models** reverted the picker to the provider default.
Neither selectbox carried a Streamlit key, so the widget's identity was derived
from its parameters — and refreshing the model list changed those, which
Streamlit treats as a different widget and resets.

The reset was the visible half. The damaging half was that the sidebar went on
displaying the default while the query, and the image-prompt rewrite, both used
it — so answers came back from a model you had not chosen, with nothing to
indicate the substitution. Both selectboxes now keep their value in session
state, with the stored choice validated against the current model list before
the widget renders, so switching provider cannot leave a stale value behind.

**The Apple runtime's fallback gateway address was wrong.** Containers reach
host services — the LLM, the image server — over the vmnet gateway, and the
fallback used when the runtime is not yet running pointed at the wrong subnet.
It was `192.168.65.1`, which is Docker Desktop's gateway, not the
`192.168.64.0/24` that macOS's vmnet framework actually allocates.

Live detection covered the usual case, so this only mattered on a cold start —
but there too it failed quietly, with the worker unable to reach the LLM and
answers arriving without synthesis rather than an error.

## Upgrading

Nothing is required. Both fixes take effect on the next run; the Docker runtime
is unaffected by the gateway change.

If you run the chat UI from a container image rather than the host, rebuild it
to pick up the picker fix — `make build-image` — since the UI is baked in.

---

_Full changelog: [CHANGELOG.md](CHANGELOG.md)_
