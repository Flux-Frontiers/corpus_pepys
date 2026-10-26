"""The test fixture's index layout matches a real ``make build-index`` index.

``conftest.py`` writes its Pepys-shaped index with only the columns the
handler reads, so the handler tests run without the diary-kg stack. This file
keeps that honest: when a real index is present (``.diarykg/`` at the repo
root, built by ``make build-index``), every column the fixture relies on must
exist there too. Without a real index (CI, a fresh clone) it skips.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import handler
import pytest
from conftest import GRAPH_EDGE_COLUMNS, GRAPH_NODE_COLUMNS, VECTOR_META_COLUMNS

_REAL = Path(__file__).resolve().parents[1] / ".diarykg"

pytestmark = pytest.mark.skipif(
    not (_REAL / "graph.sqlite").exists(), reason="no built .diarykg index (make build-index)"
)


def _columns(db: Path, table: str) -> set[str]:
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as con:
        return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}


def test_graph_has_every_column_the_fixture_relies_on():
    db = _REAL / "graph.sqlite"
    assert set(GRAPH_NODE_COLUMNS) <= _columns(db, "nodes")
    assert set(GRAPH_EDGE_COLUMNS) <= _columns(db, "edges")


def test_vectors_carry_the_metadata_the_handler_reads():
    assert set(VECTOR_META_COLUMNS) <= _columns(_REAL / "vectors.sqlite", "vec_meta")
    assert VECTOR_META_COLUMNS == handler._VECTOR_META_COLUMNS


def test_real_chunks_are_timestamped_as_the_fixture_assumes():
    with sqlite3.connect(f"file:{_REAL / 'graph.sqlite'}?mode=ro", uri=True) as con:
        unstamped = con.execute(
            "SELECT COUNT(*) FROM nodes WHERE kind = 'chunk' AND (timestamp IS NULL OR timestamp = '')"
        ).fetchone()[0]
    assert unstamped == 0
