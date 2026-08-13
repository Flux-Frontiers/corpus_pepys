"""Unit tests for docker/chat.py — synthesis-model filtering and live stats.

streamlit, httpx's transport, and the KGRAG stack are stubbed by conftest.py so
this suite runs without a browser, a worker, or a full KGRAG environment.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import chat  # docker/ is on sys.path via conftest
import httpx
import pytest

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
# _fetch_models
# ---------------------------------------------------------------------------


class TestFetchModels:
    @staticmethod
    def _client(models, default):
        client = MagicMock()
        client.list_models = MagicMock(return_value=(models, default))
        return MagicMock(return_value=client)

    def test_blocklisted_models_dropped_from_the_dropdown(self):
        factory = self._client(["qwen3-4b", "deepseek-r1:8b", "nomic-embed-text"], "qwen3-4b")
        with patch.object(chat, "WorkerClient", factory):
            models, default = chat._fetch_models("http://w:8000", "")
        assert models == ["qwen3-4b"]
        assert default == "qwen3-4b"

    def test_blocklisted_default_replaced_with_first_allowed_model(self):
        # oMLX reporting a reasoning model as its default is what put raw
        # chain-of-thought in the answer pane.
        factory = self._client(["gpt-oss-20b", "qwen3-4b", "llama3.1"], "gpt-oss-20b")
        with patch.object(chat, "WorkerClient", factory):
            models, default = chat._fetch_models("http://w:8000", "")
        assert "gpt-oss-20b" not in models
        assert default == "qwen3-4b"

    def test_all_models_blocked_yields_empty_list_and_default(self):
        factory = self._client(["deepseek-r1", "nomic-embed-text"], "deepseek-r1")
        with patch.object(chat, "WorkerClient", factory):
            models, default = chat._fetch_models("http://w:8000", "")
        assert models == []
        assert default == ""

    def test_allowed_default_preserved(self):
        factory = self._client(["a-model", "b-model"], "b-model")
        with patch.object(chat, "WorkerClient", factory):
            _, default = chat._fetch_models("http://w:8000", "")
        assert default == "b-model"


# ---------------------------------------------------------------------------
# _fetch_stats
# ---------------------------------------------------------------------------


class TestFetchStats:
    @staticmethod
    def _post(payload=None, exc=None):
        def _fake(url, json=None, timeout=None):
            _fake.sent_url = url
            _fake.sent = json
            if exc is not None:
                raise exc
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json = MagicMock(return_value=payload)
            return resp

        return _fake

    def test_unwraps_runpod_output_envelope(self):
        post = self._post({"output": {"entries": 3355, "chunks": 7282}})
        with patch.object(httpx, "post", post):
            stats = chat._fetch_stats("http://w:8000", "")
        assert stats == {"entries": 3355, "chunks": 7282}

    def test_accepts_a_bare_payload_without_the_envelope(self):
        post = self._post({"entries": 10})
        with patch.object(httpx, "post", post):
            assert chat._fetch_stats("http://w:8000", "") == {"entries": 10}

    def test_secret_included_only_when_configured(self):
        post = self._post({"output": {}})
        with patch.object(httpx, "post", post):
            chat._fetch_stats("http://w:8000", "s3cret")
        assert post.sent["input"]["secret"] == "s3cret"

        post = self._post({"output": {}})
        with patch.object(httpx, "post", post):
            chat._fetch_stats("http://w:8000", "")
        assert "secret" not in post.sent["input"]

    def test_offline_worker_degrades_to_empty_dict(self):
        post = self._post(exc=httpx.ConnectError("refused"))
        with patch.object(httpx, "post", post):
            assert chat._fetch_stats("http://w:8000", "") == {}

    def test_worker_error_payload_treated_as_unavailable(self):
        # An unauthorized or index-missing reply must render "stats
        # unavailable", not "0 entries · 0 indexed chunks".
        post = self._post({"output": {"error": "unauthorized"}})
        with patch.object(httpx, "post", post):
            assert chat._fetch_stats("http://w:8000", "") == {}

    def test_trailing_slash_in_endpoint_does_not_double_up(self):
        post = self._post({"output": {}})
        with patch.object(httpx, "post", post):
            chat._fetch_stats("http://w:8000/", "")
        assert post.sent_url == "http://w:8000/runsync"
