"""Tests for docker/chat.py: synthesis-model filtering and live stats.

The worker calls run end to end: :func:`live_worker` answers each ``/runsync``
POST by calling the real ``handler.handler`` (on the real Pepys-shaped index
``conftest.py`` builds) and returns a real ``httpx.Response`` in RunPod's
``{"output": ...}`` envelope. chat.py, kg_utils' ``WorkerClient`` and
``kg_utils.worker`` all run as installed; the HTTP hop is the only thing
replaced. An offline worker is a real refused connection.
"""

from __future__ import annotations

import chat  # docker/ is on sys.path via conftest
import handler
import httpx
import pytest
from test_handler import RecordingSynth

WORKER = "http://worker:8000"


@pytest.fixture(autouse=True)
def _fresh_streamlit_cache():
    """``_fetch_models`` and ``_fetch_stats`` are ``st.cache_data``: clear it so
    one test's worker answer is not another test's."""
    import streamlit as st

    st.cache_data.clear()
    yield
    st.cache_data.clear()


@pytest.fixture
def live_worker(monkeypatch):
    """Route ``httpx.post`` to the real handler, as the RunPod server would.

    :return: The list of (url, payload) pairs posted, for assertions.
    """
    sent: list[tuple[str, dict]] = []

    def post(url, json=None, timeout=None):
        sent.append((url, json))
        request = httpx.Request("POST", url, json=json)
        return httpx.Response(200, json={"output": handler.handler(json)}, request=request)

    monkeypatch.setattr(httpx, "post", post)
    return sent


# ---------------------------------------------------------------------------
# _is_synth_model
# ---------------------------------------------------------------------------


class TestIsSynthModel:
    @pytest.mark.parametrize(
        "model_id",
        [
            "Qwen3-4B-Instruct-2507-MLX-8bit",
            "llama3.1:8b",
            "gpt-4o-mini",
            "mistral-small",
        ],
    )
    def test_ordinary_chat_models_allowed(self, model_id):
        assert chat._is_synth_model(model_id)

    @pytest.mark.parametrize(
        "model_id",
        [
            "Agents-A1-32B",
            "deepseek-r1:14b",
            "gpt-oss-20b",
            "markitdown-1b",
            "nomic-embed-text",
            "mxbai-embed-large",
            "Qwen3-Embedding-0.6B",
        ],
    )
    def test_reasoning_and_non_chat_models_blocked(self, model_id):
        assert not chat._is_synth_model(model_id)

    def test_matching_is_case_insensitive(self):
        assert not chat._is_synth_model("DEEPSEEK-R1")
        assert not chat._is_synth_model("DeepSeek-R1:70b")

    def test_blocklist_matches_as_substring_anywhere(self):
        assert not chat._is_synth_model("hosted/team/nomic-embed-text-v1.5")


# ---------------------------------------------------------------------------
# _fetch_models -- through WorkerClient, kg_utils.worker and the handler
# ---------------------------------------------------------------------------


class TestFetchModels:
    @staticmethod
    def _serve(monkeypatch, models, default):
        class Listing(RecordingSynth):
            def list_models(self):
                return models

        synth = Listing()
        synth._cfg.resolved_model = lambda: default
        monkeypatch.setattr(handler, "_text_synth", synth)

    def test_blocklisted_models_dropped_from_the_dropdown(self, live_worker, monkeypatch):
        self._serve(monkeypatch, ["qwen3-4b", "deepseek-r1:8b", "nomic-embed-text"], "qwen3-4b")
        assert chat._fetch_models(WORKER, "") == (["qwen3-4b"], "qwen3-4b")
        assert live_worker[0][1]["input"]["op"] == "models"

    def test_blocklisted_default_replaced_with_first_allowed_model(self, live_worker, monkeypatch):
        # oMLX reporting a reasoning model as its default is what put raw
        # chain-of-thought in the answer pane.
        self._serve(monkeypatch, ["gpt-oss-20b", "qwen3-4b", "llama3.1"], "gpt-oss-20b")
        models, default = chat._fetch_models(WORKER, "")
        assert "gpt-oss-20b" not in models
        assert default == "qwen3-4b"

    def test_all_models_blocked_yields_empty_list_and_default(self, live_worker, monkeypatch):
        self._serve(monkeypatch, ["deepseek-r1", "nomic-embed-text"], "deepseek-r1")
        assert chat._fetch_models(WORKER, "") == ([], "")

    def test_allowed_default_preserved(self, live_worker, monkeypatch):
        self._serve(monkeypatch, ["a-model", "b-model"], "b-model")
        assert chat._fetch_models(WORKER, "")[1] == "b-model"


# ---------------------------------------------------------------------------
# _fetch_stats -- the sidebar's counts, from the real index
# ---------------------------------------------------------------------------


class TestFetchStats:
    def test_reports_the_served_index(self, live_worker):
        stats = chat._fetch_stats(WORKER, "")
        assert stats == handler._stats()
        assert stats["entries"] == 3 and stats["chunks"] == 4

    def test_secret_sent_and_honoured(self, live_worker, monkeypatch):
        monkeypatch.setattr(handler, "HANDLER_SECRET", "s3cret")  # pragma: allowlist secret
        assert chat._fetch_stats(WORKER, "s3cret")["entries"] == 3
        assert live_worker[-1][1]["input"]["secret"] == "s3cret"  # pragma: allowlist secret
        # Without it the worker says "unauthorized": the sidebar shows
        # "stats unavailable", not "0 entries".
        assert chat._fetch_stats(WORKER, "") == {}
        assert "secret" not in live_worker[-1][1]["input"]

    def test_missing_index_treated_as_unavailable(self, live_worker, monkeypatch, tmp_path):
        monkeypatch.setattr(handler, "_PEPYS_SQLITE", tmp_path / "absent.sqlite")
        assert chat._fetch_stats(WORKER, "") == {}

    def test_offline_worker_degrades_to_empty_dict(self):
        # Port 9 (discard) is closed: a real refused connection.
        assert chat._fetch_stats("http://127.0.0.1:9", "") == {}

    def test_accepts_a_bare_payload_without_the_envelope(self, monkeypatch):
        def post(url, json=None, timeout=None):
            return httpx.Response(
                200, json=handler.handler(json), request=httpx.Request("POST", url)
            )

        monkeypatch.setattr(httpx, "post", post)
        assert chat._fetch_stats(WORKER, "") == handler._stats()

    def test_trailing_slash_in_endpoint_does_not_double_up(self, live_worker):
        chat._fetch_stats(WORKER + "/", "")
        assert live_worker[0][0] == WORKER + "/runsync"
