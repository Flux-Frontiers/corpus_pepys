"""
Test setup for docker/handler.py: a real Pepys-shaped index, and fakes only
for what this environment genuinely does not have.

The handler is tested against the real stack wherever it is installed:

- **kg_utils is real.** Synthesis backends, ``handle_aux_ops`` and the
  ``SqliteVecBackend`` the handler searches are the installed package. A test
  that exercises synthesis swaps in a recording synthesizer at the network
  boundary, never the module.
- **The index is real.** :func:`build_pepys_index` writes a small diary corpus
  the way ``diarykg build`` lays it out: frontmatter ``.md`` files under
  ``.diarykg/corpus/``, the graph through doc-kg's ``GraphStore``, the
  per-chunk ``timestamp`` through DiaryKG's own ``_enrich_metadata``, and the
  vectors through kg_utils' ``SqliteVecBackend``. Only the embedding model is
  replaced, by :class:`KeywordEmbedder`, so nothing is downloaded.

Two modules are faked because they are not installed outside the container
(``pyproject.toml`` keeps them out on purpose; ``scripts/check_pins.py`` lists
kg-rag as container-only):

- ``runpod``: the serverless harness. ``handler.py`` only calls
  ``runpod.serverless.start`` under ``__main__``.
- ``kg_rag``: ``KGRegistry``/``KGEntry``/``KGKind`` and the sentence-transformer
  wrapper. The fake registry records what the handler registers, and the
  embedder is :class:`KeywordEmbedder`.

The fixture index is built at collection time, before any test imports
``handler``, because the handler opens its store at import.
"""

from __future__ import annotations

import atexit
import enum
import math
import os
import shutil
import sys
import tempfile
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# docker/ is not a package; add it to sys.path so `import handler` works.
_DOCKER_DIR = Path(__file__).parent.parent / "docker"
if str(_DOCKER_DIR) not in sys.path:
    sys.path.insert(0, str(_DOCKER_DIR))


# ---------------------------------------------------------------------------
# A deterministic embedder: one dimension per keyword, plus a constant
# ---------------------------------------------------------------------------


class KeywordEmbedder:
    """Bag-of-keywords embedding, so similarity is predictable in tests.

    Each vocabulary word is one dimension (1.0 when the text contains it); a
    small constant dimension keeps every vector non-zero, so cosine distance is
    always defined.

    :param model: Accepted for the ``SentenceTransformerEmbedder`` signature.
    """

    VOCAB = ("fire", "burning", "city", "plague", "mortality", "navy", "office", "wife", "dinner")

    def __init__(self, model: str = "") -> None:
        self.model_name = model

    @property
    def dim(self) -> int:
        return len(self.VOCAB) + 1

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            low = t.lower()
            out.append([1.0 if w in low else 0.0 for w in self.VOCAB] + [0.05])
        return out


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity, for asserting the scores the store reports."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


# ---------------------------------------------------------------------------
# A small Pepys-shaped index, built with the real fleet code
# ---------------------------------------------------------------------------

#: (file name, timestamp, category, [chunk texts]) -- one diary day per file,
#: as ``diarykg build`` writes them.
PEPYS_ENTRIES: list[tuple[str, str, str, list[str]]] = [
    (
        "entry_0000_chunk_0.md",
        "1660-03-12T08:00",
        "pepys_naval",
        [
            "Up early and to the Navy Office, where I sat all the morning.",
            "Home to dinner with my wife, and then to bed.",
        ],
    ),
    (
        "entry_0001_chunk_0.md",
        "1665-08-31T08:00",
        "pepys_public",
        ["The plague is grown very great in the city; the bills of mortality rise."],
    ),
    (
        "entry_0002_chunk_0.md",
        "1666-09-02T08:00",
        "pepys_public",
        ["Up by five and saw the great fire burning down the city by the bridge."],
    ),
]


def build_pepys_index(root: Path) -> dict[str, Any]:
    """Write a Pepys-shaped DiaryKG index under ``root/.diarykg``.

    :param root: Corpus root (what ``PEPYS_KG_ROOT`` names).
    :return: What was written: chunk ids by text, the document ids, the number
        of edges, and the number of vectors.
    """
    from diary_kg import DiaryKG
    from doc_kg.dockg import DocEdge, DocNode
    from doc_kg.store import GraphStore
    from kg_utils.vector_backend import SqliteVecBackend

    kg_dir = root / ".diarykg"
    corpus = kg_dir / "corpus"
    corpus.mkdir(parents=True)

    nodes: list[DocNode] = []
    edges: list[DocEdge] = []
    chunk_ids: dict[str, str] = {}
    doc_ids: list[str] = []
    for fname, ts, category, chunks in PEPYS_ENTRIES:
        body = "\n\n".join(chunks)
        (corpus / fname).write_text(
            f"---\nsource_file: data/pepys.txt\ntimestamp: {ts}\ncategory: {category}\n"
            f"context: General\n---\n\n{body}\n",
            encoding="utf-8",
        )
        doc_id = f"doc:{fname}"
        doc_ids.append(doc_id)
        nodes.append(
            DocNode(
                id=doc_id,
                kind="document",
                name=fname,
                title=None,
                file_path=fname,
                char_start=0,
                char_end=len(body),
                heading_level=None,
                text=body,
            )
        )
        prev = None
        for i, text in enumerate(chunks):
            cid = f"chunk:{fname}:{i:04d}"
            chunk_ids[text] = cid
            nodes.append(
                DocNode(
                    id=cid,
                    kind="chunk",
                    name=f"chunk:{i + 1:04d}",
                    title=None,
                    file_path=fname,
                    char_start=0,
                    char_end=len(text),
                    heading_level=None,
                    text=text,
                )
            )
            edges.append(DocEdge(src=doc_id, rel="CONTAINS", dst=cid))
            if prev:
                edges.append(DocEdge(src=prev, rel="NEXT", dst=cid))
            prev = cid
    # A non-chunk, non-document node, as the real index has topics and entities.
    nodes.append(
        DocNode(
            id="topic:pepys_public",
            kind="topic",
            name="pepys_public",
            title=None,
            file_path=None,
            char_start=None,
            char_end=None,
            heading_level=None,
            text="",
        )
    )

    store = GraphStore(kg_dir / "graph.sqlite")
    store.write(nodes, edges, wipe=True, quiet=True)
    store.close()

    # DiaryKG stamps timestamp/category onto chunks from the corpus frontmatter.
    DiaryKG(root)._enrich_metadata()

    emb = KeywordEmbedder()
    rows = []
    for n in nodes:
        if n.kind in ("chunk", "document"):
            rows.append(
                {
                    "id": n.id,
                    "kind": n.kind,
                    "name": n.name,
                    "title": n.title,
                    "file_path": n.file_path,
                    "vector": emb.embed_texts([n.text or ""])[0],
                }
            )
    backend = SqliteVecBackend(
        kg_dir / "vectors.sqlite", dim=emb.dim, meta_columns=("kind", "name", "title", "file_path")
    )
    backend.open()
    backend.upsert(rows)
    backend.close()
    return {"chunk_ids": chunk_ids, "doc_ids": doc_ids, "edges": len(edges), "vectors": len(rows)}


_PEPYS_ROOT = Path(tempfile.mkdtemp(prefix="pepys-kg-"))
atexit.register(shutil.rmtree, _PEPYS_ROOT, ignore_errors=True)
PEPYS_INDEX = build_pepys_index(_PEPYS_ROOT)
os.environ["PEPYS_KG_ROOT"] = str(_PEPYS_ROOT)
os.environ.pop("HANDLER_SECRET", None)


# ---------------------------------------------------------------------------
# Fakes for the two container-only modules
# ---------------------------------------------------------------------------


def _module(name: str, **attrs: Any) -> types.ModuleType:
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


class _Serverless:
    started: list[dict] = []

    @classmethod
    def start(cls, config: dict) -> None:
        cls.started.append(config)


_module("runpod", serverless=_Serverless)


class KGKind(enum.Enum):
    DIARY = "diary"


@dataclass
class KGEntry:
    id: str
    name: str
    kind: KGKind
    repo_path: Path
    venv_path: Path
    sqlite_path: Path
    vectors_path: Path | None
    created_at: Any
    updated_at: Any


@dataclass
class KGRegistry:
    db_path: Path
    entries: list[KGEntry] = field(default_factory=list)

    def register(self, entry: KGEntry) -> None:
        self.entries.append(entry)


_module("kg_rag")
_module("kg_rag.primitives", KGEntry=KGEntry, KGKind=KGKind)
_module("kg_rag.registry", KGRegistry=KGRegistry)
_module("kg_rag._embedders", SentenceTransformerEmbedder=KeywordEmbedder)
