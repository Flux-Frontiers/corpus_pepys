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
SYNTH_BACKEND     omlx | ollama | openai  (default: omlx)
SYNTH_ENDPOINT    Override synthesis base URL  (legacy alias: VLLM_ENDPOINT_URL)
SYNTH_API_KEY     Bearer token / OpenAI key  (legacy alias: VLLM_API_KEY)
SYNTH_MODEL       Model ID override  (legacy alias: VLLM_MODEL)
OLLAMA_ENDPOINT   Ollama base URL  (default: http://host.docker.internal:11434/v1)
OPENAI_API_KEY    OpenAI API key
IMAGE_BACKEND     mflux-local | mflux-serve | openai  (default: mflux-serve)
IMAGE_ENDPOINT    mflux-serve base URL  (default: http://host.docker.internal:8090)
IMAGE_STEPS       Default inference steps  (default: 4)
SYNTH_MAX_K       Max snippets fed to synthesis  (default: 12)

Request schema
--------------
{
  "query":          str   — natural-language query (required, except for op-only requests)
  "secret":         str   — required when HANDLER_SECRET is set
  "corpus":         str   — "diary" | "all"  (default: "all")
  "k":              int   — top-k hits  (default: 8)
  "min_score":      float — drop hits below this score  (default: 0.0)
  "semantic_floor": float — discard KG if best hit is below this  (default: 0.0)
  "synthesize":     bool  — generate a narrative answer  (default: false)
  "model":          str   — model ID override for this request
  "backend":        str   — omlx | ollama | openai  (overrides SYNTH_BACKEND for this request)
  "op":             str   — "models"  → {"models": [...], "default": ...}
                            "rewrite" → {"prompt": "...", "error": ...}
                            "imagine" → {"image_b64": "...", "prompt": ..., "aspect_ratio": ...}
  "text":           str   — passage to rewrite (required when op="rewrite")
  "prompt":         str   — image prompt (required when op="imagine")
  "aspect_ratio":   str   — one of 1:1 3:2 2:3 16:9 9:16 4:3 3:4  (default: 3:2)
  "steps":          int   — inference steps  (default: IMAGE_STEPS)
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
from kg_utils.synthesis import image_synthesizer_from_env, text_synthesizer_from_env

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PEPYS_KG_ROOT = Path(os.environ.get("PEPYS_KG_ROOT", "/workspace/pepys"))
REGISTRY_PATH = Path("/tmp/pepys_worker/registry.sqlite")
SYNTH_MAX_K = int(os.environ.get("SYNTH_MAX_K", "12"))
EMBED_MODEL = os.environ.get("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
HANDLER_SECRET = os.environ.get("HANDLER_SECRET", "")

_PEPYS_SQLITE = PEPYS_KG_ROOT / ".diarykg" / "graph.sqlite"
_PEPYS_LANCEDB = PEPYS_KG_ROOT / ".diarykg" / "lancedb"

_PEPYS_RAG_SYSTEM = (
    "You are a knowledgeable guide to Samuel Pepys' diary. "
    "Answer the question using ONLY the provided diary excerpts. "
    "Be concise and specific. Quote dates when relevant."
)


def _normalize_omlx_endpoint(endpoint: str) -> str:
    """Ensure oMLX endpoint is OpenAI-compatible base URL ending with /v1."""
    ep = (endpoint or "").strip().rstrip("/")
    if not ep:
        return ""
    if ep.endswith("/v1"):
        return ep
    return f"{ep}/v1"


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

print("[startup] initialising DiaryKG for content lookup ...")
from diary_kg.kg import DiaryKG  # noqa: E402

_diarykg = DiaryKG(root=PEPYS_KG_ROOT, model=EMBED_MODEL)

print("[startup] initialising synthesis backends ...")
_text_synth = text_synthesizer_from_env()
_image_synth = image_synthesizer_from_env()
print("[startup] ready")


# ---------------------------------------------------------------------------
# Per-request backend factory
# ---------------------------------------------------------------------------


def _synth_for_backend(backend_str: str):
    """Return a TextSynthesizer for the requested backend, using env-aware endpoints.

    Falls back to the startup default synthesizer on unknown or empty backend_str.
    """
    from kg_utils.synthesis import TextSynthesizer
    from kg_utils.synthesis._config import TextBackend, TextConfig

    backend_str = (backend_str or "").strip().lower()
    if not backend_str:
        return _text_synth
    try:
        backend = TextBackend(backend_str)
    except ValueError:
        return _text_synth

    if backend == TextBackend.OMLX:
        endpoint = os.environ.get("SYNTH_ENDPOINT") or os.environ.get("VLLM_ENDPOINT_URL") or ""
        endpoint = _normalize_omlx_endpoint(endpoint)
        api_key = os.environ.get("SYNTH_API_KEY") or os.environ.get("VLLM_API_KEY") or ""
        model = os.environ.get("SYNTH_MODEL") or os.environ.get("VLLM_MODEL") or ""
        return TextSynthesizer(
            TextConfig(backend=backend, endpoint=endpoint, api_key=api_key, model=model)
        )
    elif backend == TextBackend.OLLAMA:
        endpoint = os.environ.get("OLLAMA_ENDPOINT") or ""
        return TextSynthesizer(TextConfig(backend=backend, endpoint=endpoint))
    elif backend == TextBackend.OPENAI:
        api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("SYNTH_API_KEY") or ""
        return TextSynthesizer(TextConfig(backend=backend, api_key=api_key))

    return _text_synth


def _image_for_backend(backend_str: str):
    """Return an ImageSynthesizer for the requested image backend.

    Falls back to the startup default on unknown or empty backend_str.
    """
    from kg_utils.synthesis import ImageSynthesizer
    from kg_utils.synthesis._config import ImageBackend, ImageConfig

    backend_str = (backend_str or "").strip().lower()
    if not backend_str:
        return _image_synth
    try:
        backend = ImageBackend(backend_str)
    except ValueError:
        return _image_synth

    if backend == ImageBackend.OPENAI:
        api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("IMAGE_API_KEY") or ""
        return ImageSynthesizer(ImageConfig(backend=backend, api_key=api_key))
    elif backend == ImageBackend.MFLUX_SERVE:
        server_url = os.environ.get("IMAGE_ENDPOINT") or ""
        return ImageSynthesizer(ImageConfig(backend=backend, server_url=server_url))
    elif backend == ImageBackend.MFLUX_LOCAL:
        model = os.environ.get("IMAGE_MODEL") or os.environ.get("GUTENKG_IMAGE_MODEL") or ""
        return ImageSynthesizer(ImageConfig(backend=backend, model=model))

    return _image_synth


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
    """Attach each hit's full source text under a ``content`` key via batched SQLite lookup."""
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


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


def handler(job: dict) -> dict:
    inp = job.get("input", {})

    if HANDLER_SECRET and inp.get("secret") != HANDLER_SECRET:
        return {"error": "unauthorized"}

    if inp.get("op") == "models":
        synth = _synth_for_backend(inp.get("backend", ""))
        return {"models": synth.list_models(), "default": synth._cfg.resolved_model()}

    if inp.get("op") == "rewrite":
        text = (inp.get("text") or "").strip()
        if not text:
            return {"error": "rewrite requires a non-empty 'text'"}
        synth = _synth_for_backend(inp.get("backend", ""))
        model_override = (inp.get("model") or "").strip() or None
        prompt, error = synth.rewrite_for_image(text, model=model_override)
        return {"prompt": prompt, "error": error}

    if inp.get("op") == "imagine":
        prompt = (inp.get("prompt") or "").strip()
        if not prompt:
            return {"error": "imagine requires a non-empty 'prompt'"}
        aspect = inp.get("aspect_ratio", "3:2")
        seed = inp.get("seed")
        steps = inp.get("steps")
        img_synth = _image_for_backend(inp.get("image_backend", ""))
        try:
            b64 = img_synth.generate_b64(
                prompt,
                aspect_ratio=aspect,
                seed=int(seed) if seed is not None else None,
                steps=int(steps) if steps is not None else None,
            )
            return {
                "image_b64": b64,
                "prompt": prompt,
                "aspect_ratio": aspect,
                "image_model": img_synth._cfg.resolved_model(),
                "image_backend": img_synth._cfg.backend.value,
            }
        except Exception as exc:  # noqa: BLE001
            return {"error": f"image generation failed: {exc}"}

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
    active_synth = _synth_for_backend(inp.get("backend", ""))
    if synthesize:
        t0_synth = time.perf_counter()
        synthesis = active_synth.synthesize_rag(
            query, hits, model=model, max_k=SYNTH_MAX_K, system=_PEPYS_RAG_SYSTEM
        )
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
        "model": (model or active_synth._cfg.resolved_model()) if synthesize else None,
    }


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
