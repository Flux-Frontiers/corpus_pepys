"""Tests for docker/handler.py against a real Pepys-shaped index.

``conftest.py`` builds the index with the real fleet code (doc-kg's
GraphStore, DiaryKG's metadata stamping, kg_utils' SqliteVecBackend) and a
deterministic keyword embedder, so every search here runs real sqlite-vec
cosine ranking and real SQLite hydration. kg_utils is the installed package;
only the container-only ``runpod`` and ``kg_rag`` are faked. Tests change the
handler's configuration (its secret, its index path) with ``monkeypatch``,
and replace a synthesizer only where a test would otherwise call an LLM.
"""

from __future__ import annotations

import handler  # docker/ is on sys.path via conftest
import pytest
from conftest import PEPYS_ENTRIES, PEPYS_INDEX, KeywordEmbedder, cosine

FIRE = PEPYS_ENTRIES[2][3][0]
PLAGUE = PEPYS_ENTRIES[1][3][0]
NAVY, DINNER = PEPYS_ENTRIES[0][3]


def _call(**inp):
    return handler.handler({"input": inp})


class RecordingSynth:
    """Stands in for an LLM backend: records the call and answers, or raises."""

    def __init__(self, answer="an answer", error: Exception | None = None):
        self.answer, self.error, self.calls = answer, error, []

        class _Cfg:
            @staticmethod
            def resolved_model():
                return "recording-model"

        self._cfg = _Cfg()

    def synthesize_rag(self, query, hits, **kwargs):
        self.calls.append((query, hits, kwargs))
        if self.error:
            raise self.error
        return self.answer


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
    def test_hydrates_text_and_the_stamped_timestamp(self):
        hits = [{"node_id": PEPYS_INDEX["chunk_ids"][FIRE]}]
        handler._attach_diary_fields(hits)
        assert hits[0]["content"] == hits[0]["summary"] == FIRE
        # DiaryKG._enrich_metadata wrote it from the corpus frontmatter.
        assert hits[0]["timestamp"] == "1666-09-02T08:00"

    def test_hydrates_several_hits_in_place(self):
        hits = [{"node_id": PEPYS_INDEX["chunk_ids"][t]} for t in (NAVY, DINNER)]
        handler._attach_diary_fields(hits)
        assert [h["content"] for h in hits] == [NAVY, DINNER]
        assert {h["timestamp"] for h in hits} == {"1660-03-12T08:00"}

    def test_unknown_node_leaves_fields_empty(self):
        hits = [{"node_id": "chunk:nope.md:0000", "content": "old", "timestamp": "old"}]
        handler._attach_diary_fields(hits)
        assert hits[0]["content"] == ""
        assert hits[0]["timestamp"] is None

    def test_no_usable_ids_is_a_no_op(self):
        handler._attach_diary_fields([])
        hits = [{"node_id": None, "content": "kept"}]
        handler._attach_diary_fields(hits)
        assert hits == [{"node_id": None, "content": "kept"}]

    def test_missing_index_is_a_no_op(self, tmp_path, monkeypatch):
        monkeypatch.setattr(handler, "_PEPYS_SQLITE", tmp_path / "absent.sqlite")
        hits = [{"node_id": PEPYS_INDEX["chunk_ids"][FIRE], "content": "kept"}]
        handler._attach_diary_fields(hits)
        assert hits[0]["content"] == "kept"


# ---------------------------------------------------------------------------
# _semantic_search -- real sqlite-vec ranking
# ---------------------------------------------------------------------------


class TestSemanticSearch:
    def test_the_matching_entry_ranks_first(self):
        hits = handler._semantic_search("the great fire", k=3)
        assert hits[0]["node_id"] == PEPYS_INDEX["chunk_ids"][FIRE]
        assert hits[0]["content"] == FIRE

    def test_score_is_the_cosine_similarity(self):
        emb = KeywordEmbedder()
        expected = cosine(emb.embed_texts(["the great fire"])[0], emb.embed_texts([FIRE])[0])
        hits = handler._semantic_search("the great fire", k=1)
        assert hits[0]["score"] == pytest.approx(expected, abs=1e-3)

    def test_document_vectors_are_never_returned(self):
        # Each entry's document node has a vector too, and the fire document
        # matches "fire" as well as its chunk does; the kind filter drops it.
        hits = handler._semantic_search("fire burning city", k=10)
        assert hits and {h["kind"] for h in hits} == {"chunk"}
        assert not set(PEPYS_INDEX["doc_ids"]) & {h["node_id"] for h in hits}

    def test_k_limits_the_result(self):
        assert len(handler._semantic_search("city", k=2)) == 2

    def test_min_score_drops_weak_hits(self):
        everything = handler._semantic_search("plague mortality", k=4)
        strong = handler._semantic_search("plague mortality", k=4, min_score=0.5)
        assert len(strong) < len(everything)
        assert [h["node_id"] for h in strong] == [PEPYS_INDEX["chunk_ids"][PLAGUE]]

    def test_semantic_floor_discards_a_weak_set(self):
        # Nothing in the diary is about the theatre: the best hit is weak.
        assert handler._semantic_search("the theatre", k=3) != []
        assert handler._semantic_search("the theatre", k=3, semantic_floor=0.5) == []

    def test_semantic_floor_keeps_a_strong_set(self):
        assert handler._semantic_search("the great fire", k=3, semantic_floor=0.5)

    def test_no_store_returns_nothing(self, monkeypatch):
        monkeypatch.setattr(handler, "_PEPYS_STORE", None)
        assert handler._semantic_search("fire", k=5) == []


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------


class TestStartup:
    def test_the_index_is_registered_with_its_vectors(self):
        (entry,) = handler._registry.entries
        assert entry.name == "pepys" and entry.kind.name == "DIARY"
        assert entry.sqlite_path == handler._PEPYS_SQLITE
        assert entry.vectors_path == handler._PEPYS_VECTORS

    def test_the_store_opens_with_every_vector(self):
        assert handler._PEPYS_STORE.count() == PEPYS_INDEX["vectors"]


# ---------------------------------------------------------------------------
# handler -- dispatch
# ---------------------------------------------------------------------------


class TestHandlerDispatch:
    def test_unauthorized_without_or_with_the_wrong_secret(self, monkeypatch):
        monkeypatch.setattr(handler, "HANDLER_SECRET", "s3cret")  # pragma: allowlist secret
        assert _call(query="fire") == {"error": "unauthorized"}
        wrong = {"secret": "wrong"}  # pragma: allowlist secret
        assert _call(query="fire", **wrong) == {"error": "unauthorized"}

    def test_the_right_secret_searches(self, monkeypatch):
        monkeypatch.setattr(handler, "HANDLER_SECRET", "s3cret")  # pragma: allowlist secret
        result = _call(query="the great fire", secret="s3cret")  # pragma: allowlist secret
        assert result["hits"][0]["content"] == FIRE

    @pytest.mark.parametrize("inp", [{"query": ""}, {"query": "   "}, {}])
    def test_a_query_is_required(self, inp):
        assert handler.handler({"input": inp}) == {"error": "query is required"}

    def test_unknown_corpus_is_refused(self):
        result = _call(query="fire", corpus="books")
        assert "books" in result["error"]

    @pytest.mark.parametrize("corpus", ["diary", "all"])
    def test_known_corpora_search_the_diary(self, corpus):
        result = _call(query="the great fire", corpus=corpus)
        assert result["corpus"] == corpus
        assert result["hits"][0]["node_id"] == PEPYS_INDEX["chunk_ids"][FIRE]

    def test_response_carries_the_hits(self):
        result = _call(query="plague in the city", k=2)
        assert result["query"] == "plague in the city"
        assert result["kgs_queried"] == 1
        assert result["total_hits"] == len(result["hits"]) == 2
        top = result["hits"][0]
        assert top["kg_name"] == "pepys" and top["kind"] == "chunk"
        assert top["content"] == PLAGUE and top["timestamp"] == "1665-08-31T08:00"
        assert top["source_path"] == "entry_0001_chunk_0.md"
        assert isinstance(result["search_ms"], int)

    def test_min_score_and_semantic_floor_pass_through(self):
        weak = _call(query="the theatre", semantic_floor=0.5)
        assert weak["hits"] == [] and weak["total_hits"] == 0
        strong = _call(query="plague mortality", k=4, min_score=0.5)
        assert [h["content"] for h in strong["hits"]] == [PLAGUE]

    def test_k_below_one_is_clamped(self):
        assert len(_call(query="city", k=0)["hits"]) == 1


# ---------------------------------------------------------------------------
# Synthesis -- the one place a fake stands in, for the LLM call
# ---------------------------------------------------------------------------


class TestSynthesis:
    def test_off_by_default(self, monkeypatch):
        synth = RecordingSynth()
        monkeypatch.setattr(handler, "_text_synth", synth)
        result = _call(query="fire")
        assert result["synthesis"] is None and result["synthesis_error"] is None
        assert result["synthesis_ms"] is None and result["model"] is None
        assert synth.calls == []

    def test_gets_the_real_hits_and_the_pepys_prompt(self, monkeypatch):
        synth = RecordingSynth("The fire began near the bridge.")
        monkeypatch.setattr(handler, "_text_synth", synth)
        result = _call(query="the great fire", synthesize=True, k=2)
        assert result["synthesis"] == "The fire began near the bridge."
        assert result["synthesis_error"] is None
        assert result["model"] == "recording-model"
        ((query, hits, kwargs),) = synth.calls
        assert query == "the great fire"
        assert hits[0]["content"] == FIRE  # what the LLM is grounded on
        assert kwargs["system"] == handler._PEPYS_RAG_SYSTEM
        assert kwargs["max_k"] == handler.SYNTH_MAX_K

    def test_a_requested_model_is_passed_and_reported(self, monkeypatch):
        synth = RecordingSynth()
        monkeypatch.setattr(handler, "_text_synth", synth)
        result = _call(query="fire", synthesize=True, model="qwen-test")
        assert synth.calls[0][2]["model"] == "qwen-test"
        assert result["model"] == "qwen-test"

    def test_a_backend_failure_keeps_the_search(self, monkeypatch):
        monkeypatch.setattr(
            handler, "_text_synth", RecordingSynth(error=RuntimeError("connection refused"))
        )
        result = _call(query="the great fire", synthesize=True)
        assert result["synthesis"] is None
        assert result["synthesis_error"] == "RuntimeError: connection refused"
        assert result["hits"][0]["content"] == FIRE
        assert result["synthesis_ms"] is not None

    def test_the_real_default_synthesizer_is_configured_from_the_environment(self):
        from kg_utils.synthesis import TextSynthesizer

        assert isinstance(handler._text_synth, TextSynthesizer)


# ---------------------------------------------------------------------------
# _stats -- the chat sidebar's live corpus counts
# ---------------------------------------------------------------------------


class TestStats:
    def test_counts_come_from_the_served_index(self):
        stats = handler._stats()
        assert stats["entries"] == len(PEPYS_ENTRIES)  # distinct diary days
        assert stats["chunks"] == sum(len(e[3]) for e in PEPYS_ENTRIES)
        # documents + chunks + the topic node
        assert stats["nodes"] == len(PEPYS_ENTRIES) + stats["chunks"] + 1
        assert stats["edges"] == PEPYS_INDEX["edges"]
        assert stats["vectors"] == PEPYS_INDEX["vectors"]
        assert stats["embed_model"] == handler.EMBED_MODEL

    def test_the_stats_op_needs_no_query(self):
        assert _call(op="stats") == handler._stats()

    def test_the_stats_op_honours_the_secret(self, monkeypatch):
        monkeypatch.setattr(handler, "HANDLER_SECRET", "s3cret")  # pragma: allowlist secret
        assert _call(op="stats") == {"error": "unauthorized"}
        ok = _call(op="stats", secret="s3cret")  # pragma: allowlist secret
        assert ok["entries"] == len(PEPYS_ENTRIES)

    def test_vectors_zero_when_the_store_is_unopened(self, monkeypatch):
        monkeypatch.setattr(handler, "_PEPYS_STORE", None)
        assert handler._stats()["vectors"] == 0

    def test_missing_index_reports_an_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(handler, "_PEPYS_SQLITE", tmp_path / "absent.sqlite")
        assert handler._stats() == {"error": "index not found"}

    def test_corrupt_index_reports_an_error_not_raise(self, tmp_path, monkeypatch):
        db = tmp_path / "graph.sqlite"
        db.write_text("this is not a sqlite database")
        monkeypatch.setattr(handler, "_PEPYS_SQLITE", db)
        assert "error" in handler._stats()


# ---------------------------------------------------------------------------
# Aux ops go through the real kg_utils.worker
# ---------------------------------------------------------------------------


class TestAuxOps:
    def test_an_unknown_op_falls_through_to_search(self):
        result = _call(op="no-such-op", query="the great fire")
        assert result["hits"][0]["content"] == FIRE

    def test_models_op_is_answered_by_kg_utils_worker(self, monkeypatch):
        class Listing(RecordingSynth):
            def list_models(self):
                return ["m1", "m2"]

        monkeypatch.setattr(handler, "_text_synth", Listing())
        assert _call(op="models") == {"models": ["m1", "m2"], "default": "recording-model"}

    def test_rewrite_op_validates_through_kg_utils_worker(self):
        assert _call(op="rewrite") == {"error": "rewrite requires a non-empty 'text'"}

    def test_imagine_op_validates_through_kg_utils_worker(self):
        assert _call(op="imagine") == {"error": "imagine requires a non-empty 'prompt'"}
