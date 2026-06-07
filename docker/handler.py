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
IMAGE_ENDPOINT    Optional: base URL of a running mflux-serve (e.g. http://host.docker.internal:8090).
IMAGE_STEPS       Default inference steps for image generation.  Default: 6

Request schema
--------------
{
  "query":          str   — natural-language query (required, except for op-only requests)
  "secret":         str   — required when HANDLER_SECRET is set
  "corpus":         str   — "diary" | "all"  (default: "all")
  "k":              int   — top-k hits  (default: 8)
  "min_score":      float — drop hits below this score  (default: 0.0)
  "semantic_floor": float — discard KG if best hit is below this  (default: 0.0)
  "synthesize":     bool  — call vLLM for a generated answer  (default: false)
  "model":          str   — override VLLM_MODEL for this request  (default: VLLM_MODEL)
  "op":             str   — "models"  → {"models": [...], "default": ...}
                            "imagine" → {"image_b64": "...", "prompt": ..., "aspect_ratio": ...}
  "prompt":         str   — image prompt (required when op="imagine")
  "aspect_ratio":   str   — one of 1:1 3:2 2:3 16:9 9:16 4:3 3:4  (default: 3:2)
  "steps":          int   — inference steps  (default: 6)
  "seed":           int   — optional RNG seed
}
"""

from __future__ import annotations

import os
import time
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
VLLM_MODEL = os.environ.get("VLLM_MODEL", "Qwen3-4B-Instruct-2507-MLX-8bit")
# Cap snippets fed to synthesis so a large display-k can't overflow the LLM
# context window (Ollama defaults to num_ctx=4096; oMLX/vLLM are larger but
# still finite). Retrieval/display k is unaffected.
SYNTH_MAX_K = int(os.environ.get("SYNTH_MAX_K", "12"))
EMBED_MODEL = os.environ.get("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
HANDLER_SECRET = os.environ.get("HANDLER_SECRET", "")
IMAGE_ENDPOINT = os.environ.get("IMAGE_ENDPOINT", "")  # base URL of mflux-serve
IMAGE_STEPS = int(os.environ.get("IMAGE_STEPS", "4"))

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


def _attach_content(hits: list[dict]) -> None:
    """Attach each hit's full source text (``nodes.text``) under a ``content`` key.

    The diary text already lives in the DiaryKG SQLite store, keyed by node id —
    so rather than ship a truncated summary, we look up the real passage in one
    batched query and let the UI preview/expand it. No extra text is stored.
    """
    ids = [h["node_id"] for h in hits if h.get("node_id")]
    if not ids:
        return
    import sqlite3

    db = getattr(_diarykg, "_db_path", None) or (PEPYS_KG_ROOT / ".diarykg" / "graph.sqlite")
    text_by_id: dict[str, str] = {}
    try:
        with sqlite3.connect(str(db)) as con:
            placeholders = ",".join("?" * len(ids))
            for node_id, text in con.execute(
                f"SELECT id, text FROM nodes WHERE id IN ({placeholders})", ids
            ):
                text_by_id[node_id] = text or ""
    except Exception:  # noqa: BLE001
        return
    for h in hits:
        h["content"] = text_by_id.get(h["node_id"], "")


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
    except Exception:  # noqa: BLE001
        return []


_ASPECT_SIZES: dict[str, tuple[int, int]] = {
    "1:1": (1024, 1024),
    "3:2": (1536, 1024),
    "2:3": (1024, 1536),
    "16:9": (1536, 864),
    "9:16": (864, 1536),
    "4:3": (1365, 1024),
    "3:4": (1024, 1365),
}


def _imagine(inp: dict) -> dict:
    """Handle op=imagine — proxy the prompt to IMAGE_ENDPOINT (mflux-serve)."""
    if not IMAGE_ENDPOINT:
        return {
            "error": "IMAGE_ENDPOINT not configured — set it to the base URL of a running mflux-serve"
        }

    prompt = (inp.get("prompt") or "").strip()
    if not prompt:
        return {"error": "imagine requires a non-empty 'prompt'"}

    aspect = inp.get("aspect_ratio", "3:2")
    steps = int(inp.get("steps", IMAGE_STEPS))
    seed = inp.get("seed")

    width, height = _ASPECT_SIZES.get(aspect, _ASPECT_SIZES["3:2"])
    payload: dict = {
        "prompt": prompt,
        "n": 1,
        "size": f"{width}x{height}",
        "num_inference_steps": steps,
        "response_format": "b64_json",
    }
    if seed is not None:
        payload["seed"] = int(seed)

    try:
        import httpx

        resp = httpx.post(
            IMAGE_ENDPOINT.rstrip("/") + "/v1/images/generations",
            json=payload,
            timeout=httpx.Timeout(connect=5.0, read=300.0, write=10.0, pool=5.0),
        )
        resp.raise_for_status()
        b64 = resp.json()["data"][0]["b64_json"]
        return {"image_b64": b64, "prompt": prompt, "aspect_ratio": aspect}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"image server error: {exc}"}


def _synthesize(query: str, hits: list[dict], model: str | None = None) -> str | None:
    if not VLLM_ENDPOINT:
        return None
    import re

    import httpx

    snippets = [h for h in hits[:SYNTH_MAX_K] if h.get("content")]
    if not snippets:
        return None

    ctx = "\n\n".join(f"[{h.get('name', '')[:10]}]\n{h['content'].strip()}" for h in snippets)
    if not ctx:
        return None

    headers = {"Authorization": f"Bearer {VLLM_API_KEY}"} if VLLM_API_KEY else {}
    try:
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
    except Exception:  # noqa: BLE001
        return None


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

    if inp.get("op") == "imagine":
        return _imagine(inp)

    query = inp.get("query", "").strip()
    corpus = inp.get("corpus", "all")
    k = max(1, int(inp.get("k", 8)))
    min_score = float(inp.get("min_score", 0.0))
    semantic_floor = float(inp.get("semantic_floor", 0.0))
    synthesize = bool(inp.get("synthesize", False))
    model = (inp.get("model") or "").strip() or None

    if not query:
        return {"error": "query is required"}

    if corpus not in ("diary", "all"):
        return {"error": f"unknown corpus {corpus!r}; choose: diary, all"}

    from kg_rag.primitives import KGKind

    kind_filter = [KGKind.DIARY] if corpus == "diary" else None

    t0_search = time.perf_counter()

    result = _kgrag.query(
        query,
        k=k,
        kinds=kind_filter,
        min_score=min_score,
        semantic_floor=semantic_floor,
    )

    hits = [_hit_to_dict(h) for h in result.hits]
    _attach_content(hits)

    search_ms = (time.perf_counter() - t0_search) * 1000
    print(f"[query] {len(hits)} matching results found in {search_ms:.0f}ms")

    synthesis = None
    synthesis_ms: float | None = None
    if synthesize:
        t0_synth = time.perf_counter()
        synthesis = _synthesize(query, hits, model)
        synthesis_ms = (time.perf_counter() - t0_synth) * 1000
        print(f"[query] synthesis returned in {synthesis_ms:.0f}ms")

    return {
        "query": query,
        "corpus": corpus,
        "total_hits": result.total_hits,
        "kgs_queried": result.kgs_queried,
        "hits": hits,
        "search_ms": round(search_ms),
        "synthesis": synthesis,
        "synthesis_ms": round(synthesis_ms) if synthesis_ms is not None else None,
        "model": (model or VLLM_MODEL) if synthesize else None,
    }


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
