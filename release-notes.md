# Release Notes — v0.5.0

> Released: 2026-08-03

corpus_pepys can now run without Docker. Apple's native `container` CLI is
supported as an alternative runtime on Apple Silicon, driven by the same Make
targets with a single extra flag. Docker remains the default and is unchanged.

## What changed

**A second container runtime.** `make <target> RUNTIME=apple` builds and runs
the whole stack — worker, chat UI and the host-side image server — on Apple's
`container` tool instead of Docker Desktop. The image is the same
`docker/Dockerfile`, the index is the same, and query results are identical;
only the orchestration differs. `make setup RUNTIME=apple` installs the CLI and
starts its services, so a clean machine works from a fresh clone.

Two things about that runtime bite quietly, and both are handled. Each container
is its own VM, so memory is a hard ceiling rather than a share of one large
Docker VM — the defaults would be far too small for torch, the embedder and a
41K-node graph, so the targets pass 8 GB and 6 CPUs for the worker. And
`host.docker.internal` does not resolve inside these VMs, so every host-facing
endpoint is rewritten to the vmnet gateway before launch; without it, a
configuration pointing at the LLM would simply return answers with no synthesis
and no error. That gateway address is detected from the live network rather than
hardcoded, because it varies by machine in ways the CLI version does not
predict.

**Quieter worker logs.** The runpod harness logged at `DEBUG`, echoing every
handler response into the log and then truncating it at 4096 characters by
cutting the middle out. On a five-hit query that silently dropped the middle
result from the log while the actual response was complete — alarming to read
and easy to mistake for lost data. Logging now defaults to `INFO`; set
`RUNPOD_LOG_LEVEL=DEBUG` to restore the previous output.

**A corrected claim about image sizing.** The documentation stated that mflux
rounds requested dimensions down to a multiple of 32. It is 16. The original
figure was inferred from two measurements that happened to satisfy both rules,
and has been confirmed against a live server using a size that distinguishes
them.

## Upgrading

Nothing is required. Docker remains the default runtime and behaves exactly as
before.

To try the Apple runtime you need Apple Silicon, macOS 26, and the `container`
CLI — `make setup RUNTIME=apple` installs it via Homebrew if missing. Then add
`RUNTIME=apple` to any container target. See
[docs/APPLE_CONTAINERS.md](docs/APPLE_CONTAINERS.md) for the gateway caveat,
per-VM sizing, and notes on reclaiming container disk space, which grows
silently and is not cleaned up automatically.

---

_Full changelog: [CHANGELOG.md](CHANGELOG.md)_
