# © 2026 Eric G. Suchanek, PhD — Flux-Frontiers · SPDX-License-Identifier: Elastic-2.0
"""
KGRAG handler — Pepys Diary corpus.

Serves semantic search over the Samuel Pepys DiaryKG index baked into this
image at /workspace/pepys/.diarykg/.

Implements the RunPod serverless API used by kgrag/local/docker-compose.yml so
it can be driven by the same chat.py client or any compatible curl request.

Volume layout (baked in at build time)
---------------------------------------
  /workspace/pepys/
    .diarykg/
      graph.sqlite
      lancedb/

Environment variables
---------------------
EMBED_MODEL       Sentence-transformer model ID.  Default: BAAI/bge-small-en-v1.5
HANDLER_SECRET    Optional shared secret.  Requests must include {"secret": "<value>"}.
VLLM_ENDPOINT_URL Optional: Ollama / vLLM endpoint base URL for synthesis.
RUNPOD_API_KEY    Auth token for vLLM endpoint (set "ollama" for local Ollama).
VLLM_MODEL        Model ID.  Default: qwen3:4b

Request schema
--------------
{
  "query":          str   — natural-language query (required)
  "secret":         str   — required when HANDLER_SECRET is set
  "corpus":         str   — "pepys" | "all"  (default: "all")
  "k":              int   — top-k hits  (default: 8)
  "min_score":      float — drop hits below this score  (default: 0.0)
  "semantic_floor": float — discard KG if best hit is below this  (default: 0.0)
  "synthesize":     bool  — call vLLM for a generated answer  (default: false)
}
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import runpod

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PEPYS_KG_ROOT = Path(os.environ.get("PEPYS_KG_ROOT", "/workspace/pepys"))
REGISTRY_PATH = Path("/tmp/pepys_worker/registry.sqlite")
VLLM_ENDPOINT = os.environ.get("VLLM_ENDPOINT_URL", "")
RUNPOD_API_KEY = os.environ.get("RUNPOD_API_KEY", "ollama")
VLLM_MODEL = os.environ.get("VLLM_MODEL", "qwen3:4b")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
HANDLER_SECRET = os.environ.get("HANDLER_SECRET", "")

_PEPYS_SQLITE = PEPYS_KG_ROOT / ".diarykg" / "graph.sqlite"
_PEPYS_LANCEDB = PEPYS_KG_ROOT / ".diarykg" / "lancedb"


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------


def _bootstrap_registry():
    from kg_rag.primitives import KGEntry, KGKind
    from kg_rag.registry import KGRegistry

    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    reg = KGRegistry(db_path=REGISTRY_PATH)

    if not _PEPYS_SQLITE.exists():
        print(f"[bootstrap] WARNING: Pepys index not found at {_PEPYS_SQLITE}")
        print("[bootstrap]   Run 'make build-index' then rebuild the image.")
    else:
        entry = KGEntry(
            id=str(uuid.uuid4()),
            name="pepys",
            kind=KGKind.DIARY,
            repo_path=PEPYS_KG_ROOT,
            venv_path=Path("/usr"),
            sqlite_path=_PEPYS_SQLITE,
            lancedb_path=_PEPYS_LANCEDB,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        reg.register(entry)
        print(f"[bootstrap] registered pepys diary corpus ({_PEPYS_SQLITE})")

    return reg


def _make_embedder():
    from kg_rag._embedders import SentenceTransformerEmbedder

    print(f"[startup] loading embedder: {EMBED_MODEL}")
    emb = SentenceTransformerEmbedder(EMBED_MODEL)
    emb.embed_texts(["warm up"])
    print("[startup] embedder ready")
    return emb


print("[startup] bootstrapping registry ...")
_registry = _bootstrap_registry()

print("[startup] loading embedder ...")
_embedder = _make_embedder()

print("[startup] initialising KGRAG orchestrator ...")
from kg_rag.orchestrator import KGRAG  # noqa: E402

_kgrag = KGRAG(registry_path=REGISTRY_PATH, embedder=_embedder)

print("[startup] initialising DiaryKG for synthesis ...")
from diary_kg.kg import DiaryKG  # noqa: E402

_diarykg = DiaryKG(root=PEPYS_KG_ROOT, model=EMBED_MODEL)
print("[startup] ready")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hit_to_dict(hit) -> dict:
    return {
        "kg_name": hit.kg_name,
        "kg_kind": str(hit.kg_kind),
        "node_id": hit.node_id,
        "name": hit.name,
        "kind": hit.kind,
        "score": round(float(hit.score), 4),
        "summary": hit.summary,
        "source_path": hit.source_path,
    }


def _synthesize(query: str, k: int) -> str | None:
    if not VLLM_ENDPOINT:
        return None
    import re

    import httpx

    snippets = _diarykg.pack(query, k=k)
    if not snippets:
        return None

    ctx = "\n\n".join(
        f"[{s.get('timestamp', '')[:10]}]\n{s.get('content', '')}"
        for s in snippets
        if s.get("content")
    )
    if not ctx:
        return None

    resp = httpx.post(
        f"{VLLM_ENDPOINT}/v1/chat/completions",
        headers={"Authorization": f"Bearer {RUNPOD_API_KEY}"},
        json={
            "model": VLLM_MODEL,
            "think": False,  # disable qwen3 reasoning mode — keeps response clean
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a knowledgeable guide to Samuel Pepys' diary. "
                        "Answer the question using only the provided diary excerpts. "
                        "Be concise and specific. Quote dates when relevant."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Diary excerpts:\n{ctx}\n\nQuestion: {query}",
                },
            ],
            "max_tokens": 2048,
        },
        timeout=httpx.Timeout(connect=10.0, read=600.0, write=60.0, pool=10.0),
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    # Strip any <think>…</think> blocks that leak through from reasoning models.
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    return content or None


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


def handler(job: dict) -> dict:
    inp = job.get("input", {})

    if HANDLER_SECRET and inp.get("secret") != HANDLER_SECRET:
        return {"error": "unauthorized"}

    query = inp.get("query", "").strip()
    corpus = inp.get("corpus", "all")
    k = max(1, int(inp.get("k", 8)))
    min_score = float(inp.get("min_score", 0.0))
    semantic_floor = float(inp.get("semantic_floor", 0.0))
    synthesize = bool(inp.get("synthesize", False))

    if not query:
        return {"error": "query is required"}

    if corpus not in ("pepys", "all"):
        return {"error": f"unknown corpus {corpus!r}; choose: pepys, all"}

    from kg_rag.primitives import KGKind

    kind_filter = [KGKind.DIARY] if corpus == "pepys" else None

    result = _kgrag.query(
        query,
        k=k,
        kinds=kind_filter,
        min_score=min_score,
        semantic_floor=semantic_floor,
    )

    hits = [_hit_to_dict(h) for h in result.hits]
    synthesis = _synthesize(query, k) if synthesize else None

    return {
        "query": query,
        "corpus": corpus,
        "total_hits": result.total_hits,
        "kgs_queried": result.kgs_queried,
        "hits": hits,
        "synthesis": synthesis,
    }


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
