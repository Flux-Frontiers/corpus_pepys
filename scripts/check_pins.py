#!/usr/bin/env python3
# © 2026 Eric G. Suchanek, PhD — Flux-Frontiers · SPDX-License-Identifier: Elastic-2.0
"""
Verify the KG pins agree across poetry.lock, the Dockerfile and docker-compose.

The index is built locally by the [build] extra (whose versions poetry.lock
pins exactly) and read by the container (whose versions the Dockerfile ARGs
pin exactly). Those two must match: doc-kg >=0.18.2 changed the vector store
layout, so a builder older than the runtime emits an index the container
cannot open — and the failure is silent, surfacing as empty query results
rather than an error.

The pyproject floors are deliberately NOT checked. They express intent; the
lock is what `make install` actually installs, so the lock is the truth about
what built the index. `poetry update` moves the lock without touching the
Dockerfile — that is the drift this catches.

Usage:
    python scripts/check_pins.py

Exit status:
    0  all pins agree
    1  a mismatch, or a version could not be read
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "poetry.lock"
DOCKERFILE = ROOT / "docker" / "Dockerfile"
COMPOSE = ROOT / "docker" / "docker-compose.yml"

# distribution name -> Dockerfile ARG name
PINNED = {
    "diary-kg": "DIARY_KG_VERSION",
    "doc-kg": "DOC_KG_VERSION",
    "kgmodule-utils": "KGMODULE_UTILS_VERSION",
}

# Installed in the container but not a project dependency, so it has no lock
# entry to compare against. Reported for visibility, not checked.
CONTAINER_ONLY = {"kg-rag": "KG_RAG_VERSION"}


def lock_versions() -> dict[str, str]:
    """Read exact locked versions from poetry.lock.

    :returns: mapping of distribution name to locked version.
    """
    data = tomllib.loads(LOCK.read_text())
    return {pkg["name"]: pkg["version"] for pkg in data.get("package", [])}


def dockerfile_args() -> dict[str, str]:
    """Read ``ARG <NAME>_VERSION=<value>`` defaults from the Dockerfile.

    :returns: mapping of ARG name to its default value.
    """
    pattern = re.compile(r"^ARG\s+(\w+_VERSION)=(\S+)", re.MULTILINE)
    return dict(pattern.findall(DOCKERFILE.read_text()))


def compose_args() -> dict[str, str]:
    """Read build args from docker-compose.yml.

    Compose carries its own copy of some version args, which override the
    Dockerfile defaults at build time — so they must agree too.

    :returns: mapping of build-arg name to value.
    """
    pattern = re.compile(r"^\s+(\w+_VERSION):\s*(\S+)", re.MULTILINE)
    return dict(pattern.findall(COMPOSE.read_text()))


def main() -> int:
    """Compare the pins and report.

    :returns: process exit status.
    """
    locked, dockerfile, compose = lock_versions(), dockerfile_args(), compose_args()
    problems: list[str] = []

    print(f"{'package':<18} {'poetry.lock':<14} {'Dockerfile':<14} compose")
    print("-" * 62)

    for dist, arg in PINNED.items():
        lock_v = locked.get(dist)
        docker_v = dockerfile.get(arg)
        compose_v = compose.get(arg)

        if lock_v is None:
            problems.append(f"{dist}: not in poetry.lock (run 'poetry lock')")
        if docker_v is None:
            problems.append(f"{dist}: no ARG {arg} in docker/Dockerfile")
        if lock_v and docker_v and lock_v != docker_v:
            problems.append(
                f"{dist}: poetry.lock has {lock_v} but Dockerfile ARG {arg}={docker_v} "
                f"— the index would be built by {lock_v} and read by {docker_v}"
            )
        if compose_v and docker_v and compose_v != docker_v:
            problems.append(
                f"{dist}: docker-compose.yml sets {arg}={compose_v}, overriding "
                f"the Dockerfile default {docker_v} at build time"
            )

        print(f"{dist:<18} {lock_v or '—':<14} {docker_v or '—':<14} {compose_v or '—'}")

    for dist, arg in CONTAINER_ONLY.items():
        print(
            f"{dist:<18} {'(none)':<14} {dockerfile.get(arg) or '—':<14} "
            f"{compose.get(arg) or '—'}   container-only, not checked"
        )

    print()
    if problems:
        print("PIN MISMATCH:")
        for p in problems:
            print(f"  - {p}")
        return 1

    print("Pins agree: the index builder and the container runtime match.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
