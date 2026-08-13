"""Unit tests for docker/handler.py — pure helpers and handler dispatch.

Heavy dependencies (runpod, kg_rag, kg_utils) are stubbed by conftest.py so
this suite runs without a full KGRAG environment.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

import handler  # docker/ is on sys.path via conftest

# ---------------------------------------------------------------------------
# _rows_to_hits
# ---------------------------------------------------------------------------


class TestRowsToHits:
    def test_empty_rows_returns_empty(self):
        assert handler._rows_to_hits([], 0.0) == []

    def test_basic_row_shapes_hit_correctly(self):
        rows = [
            {
                "_distance": 0.1,
                "id": "abc",
                "name": "entry",
                "kind": "chunk",
                "file_path": "diary/1.md",
            }
        ]
        hits = handler._rows_to_hits(rows, 0.0)
        assert len(hits) == 1
        h = hits[0]
        assert h["kg_name"] == "pepys"
        assert h["kg_kind"] == "KGKind.DIARY"
        assert h["node_id"] == "abc"
        assert h["name"] == "entry"
        assert h["kind"] == "chunk"
        assert h["score"] == round(1.0 - 0.1, 4)
        assert h["source_path"] == "diary/1.md"
        assert h["content"] == ""
        assert h["summary"] == ""
        assert h["timestamp"] is None

    def test_score_filtered_below_min_score(self):
        rows = [{"_distance": 0.9, "id": "low"}]
        assert handler._rows_to_hits(rows, min_score=0.5) == []

    def test_score_at_min_score_boundary_is_included(self):
        rows = [{"_distance": 0.5, "id": "boundary"}]
        hits = handler._rows_to_hits(rows, min_score=0.5)
        assert len(hits) == 1

    def test_distance_1_produces_score_zero(self):
        rows = [{"_distance": 1.0, "id": "x"}]
        hits = handler._rows_to_hits(rows, 0.0)
        assert hits[0]["score"] == 0.0

    def test_distance_0_produces_score_one(self):
        rows = [{"_distance": 0.0, "id": "x"}]
        hits = handler._rows_to_hits(rows, 0.0)
        assert hits[0]["score"] == 1.0

    def test_missing_name_falls_back_to_empty_string(self):
        rows = [{"_distance": 0.2, "id": "x", "kind": "section"}]
        hits = handler._rows_to_hits(rows, 0.0)
        assert hits[0]["name"] == ""

    def test_missing_kind_defaults_to_chunk(self):
        rows = [{"_distance": 0.2, "id": "x"}]
        hits = handler._rows_to_hits(rows, 0.0)
        assert hits[0]["kind"] == "chunk"

    def test_title_used_as_name_fallback(self):
        rows = [{"_distance": 0.2, "id": "x", "title": "My Title"}]
        hits = handler._rows_to_hits(rows, 0.0)
        assert hits[0]["name"] == "My Title"

    def test_multiple_rows_preserves_order(self):
        rows = [
            {"_distance": 0.1, "id": "a"},
            {"_distance": 0.3, "id": "b"},
            {"_distance": 0.2, "id": "c"},
        ]
        hits = handler._rows_to_hits(rows, 0.0)
        assert [h["node_id"] for h in hits] == ["a", "b", "c"]

    def test_mixed_pass_and_filter(self):
        rows = [
            {"_distance": 0.1, "id": "pass"},
            {"_distance": 0.95, "id": "filtered"},
        ]
        hits = handler._rows_to_hits(rows, min_score=0.2)
        assert len(hits) == 1
        assert hits[0]["node_id"] == "pass"


# ---------------------------------------------------------------------------
# _attach_diary_fields
# ---------------------------------------------------------------------------


class TestAttachDiaryFields:
    def test_empty_hits_does_nothing(self):
        handler._attach_diary_fields([])  # must not raise

    def test_none_node_id_skipped_gracefully(self):
        hits = [{"node_id": None, "content": "", "summary": "", "timestamp": None}]
        handler._attach_diary_fields(hits)  # must not raise

    def test_nonexistent_sqlite_path_is_a_no_op(self):
        hits = [{"node_id": "n1", "content": "", "summary": "", "timestamp": None}]
        with patch.object(handler, "_PEPYS_SQLITE", handler._PEPYS_SQLITE):
            handler._attach_diary_fields(hits)  # path doesn't exist → early return
        assert hits[0]["content"] == ""

    def test_hydrates_content_from_sqlite(self, tmp_path):
        db = tmp_path / "graph.sqlite"
        with sqlite3.connect(str(db)) as con:
            con.execute("CREATE TABLE nodes (id TEXT, text TEXT, timestamp TEXT)")
            con.execute("INSERT INTO nodes VALUES ('node1', 'some diary text', '1660-01-01')")

        hits = [{"node_id": "node1", "content": "", "summary": "", "timestamp": None}]
        with patch.object(handler, "_PEPYS_SQLITE", db):
            handler._attach_diary_fields(hits)

        assert hits[0]["content"] == "some diary text"
        assert hits[0]["summary"] == "some diary text"
        assert hits[0]["timestamp"] == "1660-01-01"

    def test_missing_node_leaves_fields_empty(self, tmp_path):
        db = tmp_path / "graph.sqlite"
        with sqlite3.connect(str(db)) as con:
            con.execute("CREATE TABLE nodes (id TEXT, text TEXT, timestamp TEXT)")

        hits = [{"node_id": "no_such_node", "content": "old", "summary": "old", "timestamp": "old"}]
        with patch.object(handler, "_PEPYS_SQLITE", db):
            handler._attach_diary_fields(hits)

        assert hits[0]["content"] == ""
        assert hits[0]["timestamp"] is None

    def test_hydrates_multiple_hits(self, tmp_path):
        db = tmp_path / "graph.sqlite"
        with sqlite3.connect(str(db)) as con:
            con.execute("CREATE TABLE nodes (id TEXT, text TEXT, timestamp TEXT)")
            con.execute("INSERT INTO nodes VALUES ('n1', 'text one', '1660-01-01')")
            con.execute("INSERT INTO nodes VALUES ('n2', 'text two', '1661-02-02')")

        hits = [
            {"node_id": "n1", "content": "", "summary": "", "timestamp": None},
            {"node_id": "n2", "content": "", "summary": "", "timestamp": None},
        ]
        with patch.object(handler, "_PEPYS_SQLITE", db):
            handler._attach_diary_fields(hits)

        assert hits[0]["content"] == "text one"
        assert hits[1]["content"] == "text two"


# ---------------------------------------------------------------------------
# _semantic_search
# ---------------------------------------------------------------------------


class TestSemanticSearch:
    def test_returns_empty_when_no_store(self):
        with patch.object(handler, "_PEPYS_STORE", None):
            assert handler._semantic_search("anything", k=5) == []

    def test_semantic_floor_discards_low_scoring_set(self):
        mock_store = MagicMock()
        mock_store.search.return_value = [{"_distance": 0.9, "id": "low", "kind": "chunk"}]

        with (
            patch.object(handler, "_PEPYS_STORE", mock_store),
            patch.object(handler, "_attach_diary_fields"),
        ):
            result = handler._semantic_search("test", k=5, semantic_floor=0.5)

        assert result == []


# ---------------------------------------------------------------------------
# handler — dispatch
# ---------------------------------------------------------------------------


class TestHandlerDispatch:
    def _call(self, **kwargs):
        return handler.handler({"input": kwargs})

    # ── secret auth ─────────────────────────────────────────────────────────

    def test_unauthorized_when_secret_missing(self):
        with patch.object(handler, "HANDLER_SECRET", "s3cret"):
            result = self._call(query="test")
        assert result == {"error": "unauthorized"}

    def test_unauthorized_when_secret_wrong(self):
        with patch.object(handler, "HANDLER_SECRET", "s3cret"):
            result = self._call(query="test", secret="wrong")
        assert result == {"error": "unauthorized"}

    def test_authorized_when_secret_correct(self):
        with (
            patch.object(handler, "HANDLER_SECRET", "s3cret"),
            patch.object(handler, "_PEPYS_STORE", None),
        ):
            result = self._call(query="plague", secret="s3cret")
        assert "error" not in result

    def test_no_secret_required_when_env_unset(self):
        with (
            patch.object(handler, "HANDLER_SECRET", ""),
            patch.object(handler, "_PEPYS_STORE", None),
        ):
            result = self._call(query="fire")
        assert "error" not in result

    # ── query validation ─────────────────────────────────────────────────────

    def test_empty_query_returns_error(self):
        with patch.object(handler, "HANDLER_SECRET", ""):
            assert self._call(query="") == {"error": "query is required"}

    def test_whitespace_only_query_returns_error(self):
        with patch.object(handler, "HANDLER_SECRET", ""):
            assert self._call(query="   ") == {"error": "query is required"}

    def test_missing_query_key_returns_error(self):
        with patch.object(handler, "HANDLER_SECRET", ""):
            result = handler.handler({"input": {}})
        assert result == {"error": "query is required"}

    # ── corpus validation ────────────────────────────────────────────────────

    def test_unknown_corpus_returns_error(self):
        with (
            patch.object(handler, "HANDLER_SECRET", ""),
            patch.object(handler, "_PEPYS_STORE", None),
        ):
            result = self._call(query="test", corpus="books")
        assert "error" in result
        assert "books" in result["error"]

    def test_corpus_diary_accepted(self):
        with (
            patch.object(handler, "HANDLER_SECRET", ""),
            patch.object(handler, "_PEPYS_STORE", None),
        ):
            result = self._call(query="great fire", corpus="diary")
        assert "error" not in result

    def test_corpus_all_accepted(self):
        with (
            patch.object(handler, "HANDLER_SECRET", ""),
            patch.object(handler, "_PEPYS_STORE", None),
        ):
            result = self._call(query="plague", corpus="all")
        assert "error" not in result

    # ── response shape ───────────────────────────────────────────────────────

    def test_response_has_required_keys(self):
        with (
            patch.object(handler, "HANDLER_SECRET", ""),
            patch.object(handler, "_PEPYS_STORE", None),
        ):
            result = self._call(query="pepys diary")
        for key in ("query", "corpus", "total_hits", "kgs_queried", "hits", "search_ms"):
            assert key in result, f"missing key: {key!r}"

    def test_query_echoed_in_response(self):
        with (
            patch.object(handler, "HANDLER_SECRET", ""),
            patch.object(handler, "_PEPYS_STORE", None),
        ):
            result = self._call(query="naval office")
        assert result["query"] == "naval office"

    def test_corpus_echoed_in_response(self):
        with (
            patch.object(handler, "HANDLER_SECRET", ""),
            patch.object(handler, "_PEPYS_STORE", None),
        ):
            result = self._call(query="theatre", corpus="diary")
        assert result["corpus"] == "diary"

    def test_empty_hits_when_no_table(self):
        with (
            patch.object(handler, "HANDLER_SECRET", ""),
            patch.object(handler, "_PEPYS_STORE", None),
        ):
            result = self._call(query="anything")
        assert result["hits"] == []
        assert result["total_hits"] == 0

    def test_k_zero_clamped_to_one(self):
        with (
            patch.object(handler, "HANDLER_SECRET", ""),
            patch.object(handler, "_PEPYS_STORE", None),
        ):
            result = self._call(query="test", k=0)
        assert "error" not in result

    def test_synthesis_off_by_default(self):
        with (
            patch.object(handler, "HANDLER_SECRET", ""),
            patch.object(handler, "_PEPYS_STORE", None),
        ):
            result = self._call(query="test")
        assert result["synthesis"] is None
        assert result["synthesis_ms"] is None
        assert result["model"] is None

    def test_synthesis_called_when_requested(self):
        with (
            patch.object(handler, "HANDLER_SECRET", ""),
            patch.object(handler, "_PEPYS_STORE", None),
        ):
            result = self._call(query="fire", synthesize=True)
        assert result["synthesis"] == "mock synthesis"
        assert result["synthesis_ms"] is not None
        assert result["model"] is not None


# ---------------------------------------------------------------------------
# _stats  (the "stats" op backing the chat sidebar's live corpus counts)
# ---------------------------------------------------------------------------


def _build_index(path, rows, edges=0):
    """Create a minimal DiaryKG-shaped graph.sqlite for the stats tests."""
    with sqlite3.connect(str(path)) as con:
        con.execute("CREATE TABLE nodes (id TEXT, kind TEXT, file_path TEXT)")
        con.execute("CREATE TABLE edges (src TEXT, rel TEXT, dst TEXT)")
        con.executemany("INSERT INTO nodes VALUES (?, ?, ?)", rows)
        con.executemany(
            "INSERT INTO edges VALUES (?, ?, ?)",
            [(f"n{i}", "REL", f"n{i + 1}") for i in range(edges)],
        )


class TestStats:
    def test_missing_index_reports_error(self, tmp_path):
        with patch.object(handler, "_PEPYS_SQLITE", tmp_path / "absent.sqlite"):
            assert handler._stats() == {"error": "index not found"}

    def test_entries_are_distinct_source_files_not_chunk_count(self, tmp_path):
        # Two chunks per entry across three entries — the distinction the chat
        # sidebar renders as "N entries · M indexed chunks".
        db = tmp_path / "graph.sqlite"
        _build_index(
            db,
            [(f"c{i}", "chunk", f"corpus/{i // 2}.md") for i in range(6)],
            edges=4,
        )
        with (
            patch.object(handler, "_PEPYS_SQLITE", db),
            patch.object(handler, "_PEPYS_STORE", None),
        ):
            stats = handler._stats()

        assert stats["entries"] == 3
        assert stats["chunks"] == 6
        assert stats["nodes"] == 6
        assert stats["edges"] == 4

    def test_non_chunk_nodes_counted_in_nodes_but_not_chunks(self, tmp_path):
        db = tmp_path / "graph.sqlite"
        _build_index(
            db,
            [
                ("c1", "chunk", "corpus/1.md"),
                ("s1", "section", "corpus/1.md"),
                ("e1", "entity", None),
            ],
        )
        with (
            patch.object(handler, "_PEPYS_SQLITE", db),
            patch.object(handler, "_PEPYS_STORE", None),
        ):
            stats = handler._stats()

        assert stats["chunks"] == 1
        assert stats["entries"] == 1
        assert stats["nodes"] == 3

    def test_vector_count_read_from_open_store(self, tmp_path):
        db = tmp_path / "graph.sqlite"
        _build_index(db, [("c1", "chunk", "corpus/1.md")])
        store = MagicMock()
        store.count = MagicMock(return_value=7282)
        with (
            patch.object(handler, "_PEPYS_SQLITE", db),
            patch.object(handler, "_PEPYS_STORE", store),
        ):
            stats = handler._stats()

        assert stats["vectors"] == 7282
        assert stats["embed_model"] == handler.EMBED_MODEL

    def test_vectors_zero_when_store_unopened(self, tmp_path):
        db = tmp_path / "graph.sqlite"
        _build_index(db, [("c1", "chunk", "corpus/1.md")])
        with (
            patch.object(handler, "_PEPYS_SQLITE", db),
            patch.object(handler, "_PEPYS_STORE", None),
        ):
            assert handler._stats()["vectors"] == 0

    def test_corrupt_index_returns_error_not_raise(self, tmp_path):
        db = tmp_path / "graph.sqlite"
        db.write_text("this is not a sqlite database")
        with (
            patch.object(handler, "_PEPYS_SQLITE", db),
            patch.object(handler, "_PEPYS_STORE", None),
        ):
            stats = handler._stats()
        assert "error" in stats

    def test_handler_dispatches_stats_op_without_a_query(self, tmp_path):
        db = tmp_path / "graph.sqlite"
        _build_index(db, [("c1", "chunk", "corpus/1.md")])
        with (
            patch.object(handler, "HANDLER_SECRET", ""),
            patch.object(handler, "_PEPYS_SQLITE", db),
            patch.object(handler, "_PEPYS_STORE", None),
        ):
            result = handler.handler({"input": {"op": "stats"}})

        # Must not fall through to the query path, which would say
        # "query is required".
        assert result["entries"] == 1
        assert "error" not in result

    def test_stats_op_still_honours_handler_secret(self, tmp_path):
        db = tmp_path / "graph.sqlite"
        _build_index(db, [("c1", "chunk", "corpus/1.md")])
        with (
            patch.object(handler, "HANDLER_SECRET", "s3cret"),
            patch.object(handler, "_PEPYS_SQLITE", db),
        ):
            assert handler.handler({"input": {"op": "stats"}}) == {"error": "unauthorized"}
            ok = handler.handler({"input": {"op": "stats", "secret": "s3cret"}})
        assert ok["entries"] == 1
