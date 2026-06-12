"""
Stub heavy KGRAG dependencies so docker/handler.py can be imported in tests
without a full KGRAG environment (no runpod, kg_rag, kg_utils, or lancedb).

Stubs are injected into sys.modules at collection time — before any test file
does `import handler` — so handler.py's module-level startup code runs against
lightweight mocks instead of real services.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

# docker/ is not a package; add it to sys.path so `import handler` works.
_DOCKER_DIR = Path(__file__).parent.parent / "docker"
if str(_DOCKER_DIR) not in sys.path:
    sys.path.insert(0, str(_DOCKER_DIR))


def _stub(name: str, **attrs) -> types.ModuleType:
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


# ── runpod ───────────────────────────────────────────────────────────────────
_runpod = _stub("runpod")
_runpod_serverless = _stub("runpod.serverless", start=MagicMock())
_runpod.serverless = _runpod_serverless

# ── kg_rag ───────────────────────────────────────────────────────────────────
_stub("kg_rag")

_KGKind = MagicMock()
_KGKind.DIARY = "KGKind.DIARY"
_stub("kg_rag.primitives", KGEntry=MagicMock(return_value=MagicMock()), KGKind=_KGKind)

_mock_registry = MagicMock()
_stub("kg_rag.registry", KGRegistry=MagicMock(return_value=_mock_registry))

_mock_embedder = MagicMock()
_mock_embedder.embed_texts = MagicMock(return_value=[[0.1] * 384])
_stub("kg_rag._embedders", SentenceTransformerEmbedder=MagicMock(return_value=_mock_embedder))

# ── kg_utils ──────────────────────────────────────────────────────────────────
_stub("kg_utils")

_mock_text_synth = MagicMock()
_mock_text_synth._cfg.resolved_model = MagicMock(return_value="mock-model")
_mock_text_synth.synthesize_rag = MagicMock(return_value="mock synthesis")
_mock_image_synth = MagicMock()

_stub(
    "kg_utils.synthesis",
    text_synthesizer_from_env=MagicMock(return_value=_mock_text_synth),
    image_synthesizer_from_env=MagicMock(return_value=_mock_image_synth),
    text_synth_for_backend=MagicMock(return_value=_mock_text_synth),
    image_synth_for_backend=MagicMock(return_value=_mock_image_synth),
)

# handle_aux_ops returns None by default → not an aux op, proceed to query path
_stub("kg_utils.worker", handle_aux_ops=MagicMock(return_value=None))

# ── lancedb ───────────────────────────────────────────────────────────────────
_mock_ldb = _stub("lancedb")
_mock_ldb.connect = MagicMock(return_value=MagicMock())
