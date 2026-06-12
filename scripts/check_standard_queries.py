#!/usr/bin/env python3
"""Run Pepys standard chat queries against a live worker and validate hits.

Usage:
    python scripts/check_standard_queries.py
    python scripts/check_standard_queries.py --endpoint http://localhost:8000 --min-hits 1
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

DEFAULT_ENDPOINT = "http://localhost:8000"

STANDARD_QUERIES: list[tuple[str, str]] = [
    ("diary", "What did Pepys witness during the Great Fire of London?"),
    ("diary", "How did the plague affect daily life in London?"),
    ("diary", "Describe Pepys' work at the Navy Office"),
    ("diary", "What music did Pepys enjoy and perform?"),
    ("diary", "How did Pepys describe King Charles II at court?"),
    ("diary", "What were Pepys' favourite theatres and plays?"),
    ("diary", "How did Pepys manage his household finances?"),
    ("diary", "What was the Dutch War like from Pepys' perspective?"),
]


def _post_runsync(endpoint: str, payload: dict) -> dict:
    body = json.dumps({"input": payload}).encode("utf-8")
    req = urllib.request.Request(
        endpoint.rstrip("/") + "/runsync",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
        data = json.loads(resp.read().decode("utf-8"))
    if data.get("status") == "FAILED":
        raise RuntimeError(data.get("error") or "runsync FAILED")
    return data.get("output", data)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run standard Pepys query checks")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help="Worker base URL")
    parser.add_argument("--k", type=int, default=10, help="Top-k retrieval")
    parser.add_argument("--min-score", type=float, default=0.5, help="Min similarity score")
    parser.add_argument("--semantic-floor", type=float, default=0.0, help="KG semantic floor")
    parser.add_argument("--min-hits", type=int, default=1, help="Required minimum hits per query")
    parser.add_argument(
        "--show-top",
        type=int,
        default=3,
        help="How many top hits to print for each query",
    )
    args = parser.parse_args()

    failures = 0
    start = time.perf_counter()

    print(f"Endpoint: {args.endpoint}")
    print(f"k={args.k} min_score={args.min_score} semantic_floor={args.semantic_floor}")
    print("-" * 72)

    for corpus, query in STANDARD_QUERIES:
        payload = {
            "query": query,
            "corpus": corpus,
            "k": args.k,
            "min_score": args.min_score,
            "semantic_floor": args.semantic_floor,
            "synthesize": False,
        }
        try:
            out = _post_runsync(args.endpoint, payload)
        except urllib.error.URLError as exc:
            print(f"FAIL [{corpus}] {query}")
            print(f"  request error: {exc}")
            failures += 1
            continue
        except (RuntimeError, ValueError) as exc:
            print(f"FAIL [{corpus}] {query}")
            print(f"  unexpected error: {exc}")
            failures += 1
            continue

        if out.get("error"):
            print(f"FAIL [{corpus}] {query}")
            print(f"  worker error: {out['error']}")
            failures += 1
            continue

        hits = out.get("hits", []) or []
        total = int(out.get("total_hits", len(hits)))
        ok = total >= args.min_hits
        status = "PASS" if ok else "FAIL"
        print(f"{status} [{corpus}] hits={total} q={query}")

        for idx, hit in enumerate(hits[: args.show_top], start=1):
            name = hit.get("name") or "-"
            ts = hit.get("timestamp") or ""
            score = float(hit.get("score", 0.0))
            print(f"  {idx}. score={score:.3f} ts={ts} name={name}")

        if not ok:
            failures += 1

    elapsed_ms = round((time.perf_counter() - start) * 1000)
    print("-" * 72)
    print(f"Completed in {elapsed_ms} ms")
    if failures:
        print(f"Result: FAIL ({failures} query checks failed)")
        return 1
    print("Result: PASS (all query checks succeeded)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
