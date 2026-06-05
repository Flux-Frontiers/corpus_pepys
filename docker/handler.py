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
VLLM_ENDPOINT_URL Optional: OpenAI-compatible endpoint base URL for synthesis (oMLX, Ollama, vLLM).
VLLM_API_KEY      Optional: Bearer token for the synthesis endpoint.  Omit for Ollama.
VLLM_MODEL        Model ID.  Default: Qwen3-30B-A3B-Instruct-2507-MLX-4bit (must match a served oMLX model_id)
SYNTH_MAX_K       Max snippets fed to synthesis (guards LLM ctx window).  Default: 12

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
  "model":          str   — override VLLM_MODEL for this request  (default: VLLM_MODEL)
  "op":             str   — "models" returns {"models": [...], "default": ...} and ignores the above
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
VLLM_API_KEY = os.environ.get("VLLM_API_KEY", "")
VLLM_MODEL = os.environ.get("VLLM_MODEL", "Qwen3-30B-A3B-Instruct-2507-MLX-4bit")
# Cap snippets fed to synthesis so a large display-k can't overflow the LLM
# context window (Ollama defaults to num_ctx=4096; oMLX/vLLM are larger but
# still finite). Retrieval/display k is unaffected.
SYNTH_MAX_K = int(os.environ.get("SYNTH_MAX_K", "12"))
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


def _list_models() -> list[str]:
    """Return the model IDs the synthesis backend currently serves (empty if none)."""
    if not VLLM_ENDPOINT:
        return []
    import httpx

    headers = {"Authorization": f"Bearer {VLLM_API_KEY}"} if VLLM_API_KEY else {}
    try:
        resp = httpx.get(
            f"{VLLM_ENDPOINT}/v1/models",
            headers=headers,
            timeout=httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0),
        )
        resp.raise_for_status()
        return [m["id"] for m in resp.json().get("data", []) if m.get("id")]
    except Exception:  # pylint: disable=broad-exception-caught
        return []


def _synthesize(query: str, k: int, model: str | None = None) -> str | None:
    if not VLLM_ENDPOINT:
        return None
    import re

    import httpx

    snippets = _diarykg.pack(query, k=min(k, SYNTH_MAX_K))
    if not snippets:
        return None

    ctx = "\n\n".join(
        f"[{s.get('timestamp', '')[:10]}]\n{s.get('content', '')}"
        for s in snippets
        if s.get("content")
    )
    if not ctx:
        return None

    headers = {"Authorization": f"Bearer {VLLM_API_KEY}"} if VLLM_API_KEY else {}
    resp = httpx.post(
        f"{VLLM_ENDPOINT}/v1/chat/completions",
        headers=headers,
        json={
            "model": model or VLLM_MODEL,
            # Disable Qwen3 reasoning so the answer isn't polluted with chain-of-
            # thought. Backends differ: Ollama honours "think"; oMLX/vLLM honour
            # "chat_template_kwargs.enable_thinking". Send both; each ignores the
            # field it doesn't recognise. (<think> stripping below is a backstop.)
            "think": False,
            "chat_template_kwargs": {"enable_thinking": False},
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

    # Lightweight op: list the synthesis models currently served, so the chat UI
    # can populate a model picker. Returns the configured default too.
    if inp.get("op") == "models":
        return {"models": _list_models(), "default": VLLM_MODEL}

    query = inp.get("query", "").strip()
    corpus = inp.get("corpus", "all")
    k = max(1, int(inp.get("k", 8)))
    min_score = float(inp.get("min_score", 0.0))
    semantic_floor = float(inp.get("semantic_floor", 0.0))
    synthesize = bool(inp.get("synthesize", False))
    model = (inp.get("model") or "").strip() or None

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
    synthesis = _synthesize(query, k, model) if synthesize else None

    return {
        "query": query,
        "corpus": corpus,
        "total_hits": result.total_hits,
        "kgs_queried": result.kgs_queried,
        "hits": hits,
        "synthesis": synthesis,
        "model": (model or VLLM_MODEL) if synthesize else None,
    }


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
