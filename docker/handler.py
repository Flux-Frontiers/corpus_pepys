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
      vectors.sqlite     # sqlite-vec store (doc-kg >=0.18); legacy: lancedb/

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
import sqlite3
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import runpod
from kg_utils.synthesis import (
    image_synth_for_backend,
    image_synthesizer_from_env,
    text_synth_for_backend,
    text_synthesizer_from_env,
)
from kg_utils.worker import handle_aux_ops

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PEPYS_KG_ROOT = Path(os.environ.get("PEPYS_KG_ROOT", "/workspace/pepys"))
REGISTRY_PATH = Path("/tmp/pepys_worker/registry.sqlite")
SYNTH_MAX_K = int(os.environ.get("SYNTH_MAX_K", "12"))
EMBED_MODEL = os.environ.get("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
HANDLER_SECRET = os.environ.get("HANDLER_SECRET", "")

_PEPYS_SQLITE = PEPYS_KG_ROOT / ".diarykg" / "graph.sqlite"
_PEPYS_VECTORS = PEPYS_KG_ROOT / ".diarykg" / "vectors.sqlite"
_PEPYS_LANCEDB = PEPYS_KG_ROOT / ".diarykg" / "lancedb"

# Columns doc-kg persists alongside each vector (doc_kg.index._META_COLUMNS).
_VECTOR_META_COLUMNS = ("kind", "name", "title", "file_path")

# Populated at startup: the Pepys DiaryKG vector store (a kg_utils
# VectorBackend), used by the semantic-first retrieval path (pure cosine
# ranking, no graph-hop expansion).
_PEPYS_STORE = None

_PEPYS_RAG_SYSTEM = (
    "You are a knowledgeable guide to Samuel Pepys' diary. "
    "Answer the question using ONLY the provided diary excerpts. "
    "Be concise and specific. Quote dates when relevant."
)

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
            vectors_path=_PEPYS_VECTORS if _PEPYS_VECTORS.exists() else None,
            lancedb_path=_PEPYS_LANCEDB if _PEPYS_LANCEDB.exists() else None,
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


def _open_pepys_store() -> None:
    """Open the Pepys DiaryKG vector store for semantic-first search.

    doc-kg >=0.18 writes a sqlite-vec sidecar (``vectors.sqlite``) next to
    ``graph.sqlite``; older builds shipped a LanceDB directory instead.
    Prefer the sqlite store, fall back to LanceDB for an un-rebuilt index.
    """
    global _PEPYS_STORE
    from kg_utils.vector_backend import LanceDBBackend, SqliteVecBackend

    dim = len(_embedder.embed_texts(["dimension probe"])[0])
    if _PEPYS_VECTORS.exists():
        store = SqliteVecBackend(
            _PEPYS_VECTORS,
            dim=dim,
            meta_columns=_VECTOR_META_COLUMNS,
            check_same_thread=False,
        )
        label = f"sqlite-vec ({_PEPYS_VECTORS})"
    elif _PEPYS_LANCEDB.exists():
        store = LanceDBBackend(
            _PEPYS_LANCEDB,
            table="dockg_nodes",
            dim=dim,
            meta_columns=_VECTOR_META_COLUMNS,
        )
        label = f"lancedb ({_PEPYS_LANCEDB})"
    else:
        print(f"[startup] WARNING: no Pepys vector store at {_PEPYS_VECTORS} or {_PEPYS_LANCEDB}")
        return

    n = store.count()
    if n == 0:
        print(f"[startup] WARNING: Pepys vector store is empty: {label}")
        return
    _PEPYS_STORE = store
    print(f"[startup] opened Pepys vector store: {label} ({n} vectors)")


print("[startup] bootstrapping registry ...")
_registry = _bootstrap_registry()

print("[startup] loading embedder ...")
_embedder = _make_embedder()

print("[startup] opening Pepys vector store ...")
_open_pepys_store()

print("[startup] initialising synthesis backends ...")
_text_synth = text_synthesizer_from_env()
_image_synth = image_synthesizer_from_env()
print("[startup] ready")


# ---------------------------------------------------------------------------
# Per-request backend factory
# ---------------------------------------------------------------------------


def _synth_for_backend(backend_str: str):
    return text_synth_for_backend(backend_str, _text_synth)


def _image_for_backend(backend_str: str):
    return image_synth_for_backend(backend_str, _image_synth)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rows_to_hits(rows: list[dict], min_score: float) -> list[dict]:
    """Shape vector-store rows into hit dicts (clean content hydrated separately).

    The store's ``text`` column (when present) holds the structured *embed-text*
    (``KIND:/TITLE:/FILE:/TEXT:`` prefixed), not the clean passage — so
    ``content``/``summary`` are left empty here and filled from SQLite by
    ``_attach_diary_fields``.
    """
    hits: list[dict] = []
    for row in rows:
        score = round(1.0 - float(row.get("_distance", 1.0)), 4)
        if score < min_score:
            continue
        hits.append(
            {
                "kg_name": "pepys",
                "kg_kind": "KGKind.DIARY",
                "node_id": row.get("id"),
                "name": row.get("name") or row.get("title") or "",
                "kind": row.get("kind", "chunk"),
                "score": score,
                "summary": "",
                "source_path": row.get("file_path") or "",
                "content": "",
                "timestamp": None,
            }
        )
    return hits


def _attach_diary_fields(hits: list[dict]) -> None:
    """Hydrate clean passage text and temporal ``timestamp`` from the Pepys SQLite."""
    ids = [h["node_id"] for h in hits if h.get("node_id")]
    if not ids or not _PEPYS_SQLITE.exists():
        return
    field_by_id: dict[str, tuple[str, str | None]] = {}
    try:
        with sqlite3.connect(str(_PEPYS_SQLITE)) as con:
            placeholders = ",".join("?" * len(ids))
            rows = con.execute(
                f"SELECT id, text, timestamp FROM nodes WHERE id IN ({placeholders})", ids
            )
            for nid, text, ts in rows:
                field_by_id[nid] = (text or "", ts)
    except Exception:  # noqa: BLE001
        return
    for h in hits:
        node_id = h.get("node_id")
        if isinstance(node_id, str):
            text, ts = field_by_id.get(node_id, ("", None))
        else:
            text, ts = ("", None)
        h["content"] = text
        h["summary"] = text
        h["timestamp"] = ts


def _semantic_search(
    query: str,
    k: int,
    min_score: float = 0.0,
    semantic_floor: float = 0.0,
) -> list[dict]:
    """Pure dense (cosine) search over the Pepys DiaryKG vector store.

    Ranks every chunk/section by its *own* semantic distance to the query — no
    graph-hop expansion, so the best-matching diary passages surface on top
    instead of inheriting a flat seed score from a few graph-expanded neighbours.

    :param query: Natural-language query string.
    :param k: Number of hits to return.
    :param min_score: Drop hits whose cosine similarity is below this.
    :param semantic_floor: If the best hit is below this, discard the whole set.
    :returns: Hit dictionaries ranked best-first, shaped like ``hit_to_dict``.
    """
    if _PEPYS_STORE is None:
        return []
    qvec = _embedder.embed_texts([query])[0]
    rows = _PEPYS_STORE.search(qvec, k, where="kind IN ('chunk', 'section')")
    hits = _rows_to_hits(rows, min_score)
    _attach_diary_fields(hits)  # clean text + timestamp from SQLite
    if semantic_floor > 0.0 and hits and hits[0]["score"] < semantic_floor:
        return []
    return hits


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


def handler(job: dict) -> dict:
    inp = job.get("input", {})

    if HANDLER_SECRET and inp.get("secret") != HANDLER_SECRET:
        return {"error": "unauthorized"}

    aux_result = handle_aux_ops(inp, _synth_for_backend, _image_for_backend)
    if aux_result is not None:
        return aux_result

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

    t0_search = time.perf_counter()

    # Semantic-first: rank chunks by their own cosine distance (no graph-hop
    # expansion), so the truly closest diary passages surface on top.
    hits = _semantic_search(query, k=k, min_score=min_score, semantic_floor=semantic_floor)

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
        "total_hits": len(hits),
        "kgs_queried": 1,
        "hits": hits,
        "search_ms": round(search_ms),
        "synthesis": synthesis,
        "synthesis_ms": round(synthesis_ms) if synthesis_ms is not None else None,
        "model": (model or active_synth._cfg.resolved_model()) if synthesize else None,
    }


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
