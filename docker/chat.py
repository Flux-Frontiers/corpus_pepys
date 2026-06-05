# © 2026 Eric G. Suchanek, PhD — Flux-Frontiers · SPDX-License-Identifier: Elastic-2.0
"""
chat.py — Samuel Pepys Diary Chat Interface

Streamlit chat UI for the corpus_pepys KGRAG worker. Searches the
DiaryKG index of Samuel Pepys' diary (1660–1669) and optionally
synthesises answers via a local Ollama model.

Run with:
    streamlit run docker/chat.py

The worker must be running first:
    make run
"""

from __future__ import annotations

import httpx
import streamlit as st

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="The Diary of Samuel Pepys",
    page_icon="📔",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DIARY_COLOR = "#D4A017"  # amber — diary kind

_NODE_KIND_COLOR: dict[str, str] = {
    "chunk": "#D4A017",
    "section": "#1ABC9C",
    "entity": "#E74C3C",
}

# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

st.markdown(
    """
    <style>
    .stTabs [data-baseweb="tab-list"] { gap: 12px; }
    .stTabs [data-baseweb="tab"] { font-size: 1rem; padding: 6px 18px; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _node_kind_badge(kind: str) -> str:
    color = _NODE_KIND_COLOR.get(kind, "#95A5A6")
    return (
        f"<span style='background:{color};color:#fff;border-radius:3px;"
        f"padding:1px 6px;font-size:11px;font-weight:bold;font-family:monospace;'>"
        f"{kind}</span>"
    )


def _score_bar(score: float, width: int = 80) -> str:
    pct = min(int(score * 100), 100)
    color = "#27AE60" if score >= 0.7 else "#F39C12" if score >= 0.4 else "#E74C3C"
    return (
        f"<div style='display:inline-block;vertical-align:middle;"
        f"width:{width}px;height:8px;background:#2a2a3e;border-radius:4px;overflow:hidden;'>"
        f"<div style='width:{pct}%;height:100%;background:{color};'></div></div>"
        f"&nbsp;<small style='color:#aaa;font-size:10px;'>{score:.3f}</small>"
    )


def _render_hit_card(hit: dict) -> None:
    node_kind = hit.get("kind", "")
    name = hit.get("name", "")
    source = hit.get("source_path") or "—"
    score = float(hit.get("score", 0.0))
    summary = hit.get("summary") or ""
    summary_short = summary[:200] + ("…" if len(summary) > 200 else "")

    st.markdown(
        f"""
        <div style="background:#1e1e2e;border-left:4px solid {_DIARY_COLOR};
                    border-radius:6px;padding:10px 14px;margin-bottom:8px;">
          <span style='background:{_DIARY_COLOR};color:#fff;border-radius:4px;
                padding:2px 9px;font-size:11px;font-weight:bold;font-family:monospace;'>
            📔 diary</span>
          &nbsp;
          {_node_kind_badge(node_kind)}
          &nbsp;&nbsp;
          <b style="font-size:14px;color:#f0f0f0;">{name}</b>
          <br>
          <span style="color:#888;font-size:11px;font-family:monospace;">📄 {source}</span>
          &nbsp;&nbsp;
          {_score_bar(score)}
          {"<br><span style='color:#ccc;font-size:12px;'>" + summary_short + "</span>" if summary_short else ""}
        </div>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Worker call
# ---------------------------------------------------------------------------


class WorkerError(Exception):
    pass


def _query_worker(
    query: str,
    *,
    worker_url: str,
    k: int,
    min_score: float,
    semantic_floor: float,
    synthesize: bool,
    secret: str,
    model: str = "",
) -> dict:
    payload: dict = {
        "input": {
            "query": query,
            "corpus": "pepys",
            "k": k,
            "min_score": min_score,
            "semantic_floor": semantic_floor,
            "synthesize": synthesize,
        }
    }
    if model:
        payload["input"]["model"] = model
    if secret:
        payload["input"]["secret"] = secret

    resp = httpx.post(
        worker_url.rstrip("/") + "/runsync",
        json=payload,
        timeout=httpx.Timeout(connect=5.0, read=600.0, write=30.0, pool=5.0),
    )
    resp.raise_for_status()
    data = resp.json()

    if data.get("status") == "FAILED" or "error_type" in data:
        error_data = data.get("error", data)
        err_type = error_data.get("error_type", "Unknown")
        err_msg = error_data.get("error_message", str(error_data))
        raise WorkerError(f"{err_type}: {err_msg}")

    return data.get("output", data)


@st.cache_data(ttl=60, show_spinner=False)
def _fetch_models(worker_url: str, secret: str) -> tuple[list[str], str]:
    """Ask the worker which synthesis models are served. Returns (model_ids, default)."""
    payload: dict = {"input": {"op": "models"}}
    if secret:
        payload["input"]["secret"] = secret
    try:
        resp = httpx.post(
            worker_url.rstrip("/") + "/runsync",
            json=payload,
            timeout=httpx.Timeout(connect=5.0, read=20.0, write=5.0, pool=5.0),
        )
        resp.raise_for_status()
        out = resp.json().get("output", {})
        return out.get("models", []), out.get("default", "")
    except Exception:  # pylint: disable=broad-exception-caught
        return [], ""


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------


_SUGGESTED_QUERIES: list[str] = [
    "What did Pepys witness during the Great Fire of London?",
    "How did the plague affect daily life in London?",
    "Describe Pepys' work at the Navy Office",
    "What music did Pepys enjoy and perform?",
    "How did Pepys describe King Charles II at court?",
    "What were Pepys' favourite theatres and plays?",
    "How did Pepys manage his household finances?",
    "What was the Dutch War like from Pepys' perspective?",
]


def _init_state() -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "pending_query" not in st.session_state:
        st.session_state.pending_query = ""


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------


def _render_sidebar() -> dict:
    st.sidebar.title("📔 Pepys Diary")
    st.sidebar.markdown("Samuel Pepys · London · 1660–1669  \n3,355 entries · 7,282 indexed chunks")
    st.sidebar.markdown("---")

    st.sidebar.subheader("🔌 Worker")
    worker_url = st.sidebar.text_input(
        "Worker URL",
        value="http://localhost:8000",
        help="Base URL of the running corpus-pepys worker",
    )
    secret = st.sidebar.text_input(
        "Secret (optional)",
        value="",
        type="password",
        help="Set only when HANDLER_SECRET is configured in the worker",
    )

    st.sidebar.markdown("---")
    st.sidebar.subheader("⚙️ Search")

    k = st.sidebar.slider("Results", min_value=1, max_value=50, value=8)
    min_score = st.sidebar.slider(
        "Min score",
        min_value=0.0,
        max_value=0.9,
        value=0.0,
        step=0.05,
        help="Drop individual hits below this relevance score",
    )
    semantic_floor = st.sidebar.slider(
        "Semantic floor",
        min_value=0.0,
        max_value=0.9,
        value=0.0,
        step=0.05,
        help="Ignore results if the best match is below this score",
    )
    synthesize = st.sidebar.toggle(
        "Generate answer",
        value=False,
        help="Generate a narrative answer via Ollama (requires VLLM_ENDPOINT_URL in worker)",
    )

    model = ""
    if synthesize:
        models, default = _fetch_models(worker_url, secret)
        if models:
            default_idx = models.index(default) if default in models else 0
            model = st.sidebar.selectbox(
                "Model",
                options=models,
                index=default_idx,
                help="Synthesis model — pulled live from the worker's LLM backend",
            )
        else:
            st.sidebar.caption("⚠️ No models reported — using the worker's default.")
        if st.sidebar.button("🔄 Refresh models", use_container_width=True):
            _fetch_models.clear()
            st.rerun()

    st.sidebar.markdown("---")
    st.sidebar.subheader("💡 Try asking")
    for q in _SUGGESTED_QUERIES:
        if st.sidebar.button(q, use_container_width=True, key=f"sq_{q[:30]}"):
            st.session_state.pending_query = q

    st.sidebar.markdown("---")
    if st.sidebar.button("🗑️ Clear chat", use_container_width=True):
        st.session_state.messages = []
        st.rerun()

    return {
        "worker_url": worker_url,
        "secret": secret,
        "k": k,
        "min_score": min_score,
        "semantic_floor": semantic_floor,
        "synthesize": synthesize,
        "model": model,
    }


# ---------------------------------------------------------------------------
# Render one assistant turn
# ---------------------------------------------------------------------------


def _render_assistant_turn(result: dict) -> None:
    synthesis = result.get("synthesis")
    synthesis_error = result.get("synthesis_error")
    hits = result.get("hits", [])
    total_hits = result.get("total_hits", 0)

    if not hits:
        st.warning(
            "No diary entries matched that query — try different wording or lower the min score."
        )
        return

    if synthesis:
        st.markdown(synthesis)
        model = result.get("model")
        if model:
            st.caption(f"🤖 {model}")
    elif synthesis_error:
        st.warning(
            f"Answer generation failed — **{synthesis_error}**\n\n"
            "Check that Ollama is running and reachable, or disable **Generate answer**."
        )
    else:
        st.info("Answer generation off — see diary entries below.")

    st.caption(f"📊 {total_hits} matching passages found")

    with st.expander(f"📄 Diary entries ({len(hits)} shown)", expanded=False):
        for hit in hits:
            _render_hit_card(hit)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    _init_state()
    cfg = _render_sidebar()

    st.title("📔 The Diary of Samuel Pepys")
    st.caption(
        "Search nine years of 17th-century London — the Restoration court, the Great Plague, "
        "the Great Fire, the Navy Office, and the daily life of a remarkable man."
    )

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            if msg["role"] == "user":
                st.markdown(msg["content"])
            else:
                _render_assistant_turn(msg["result"])

    prompt = st.chat_input("Ask about Pepys' world…")
    if not prompt and st.session_state.pending_query:
        prompt = st.session_state.pending_query
        st.session_state.pending_query = ""
    if prompt:
        with st.chat_message("user"):
            st.markdown(prompt)
        st.session_state.messages.append({"role": "user", "content": prompt, "result": None})

        with st.chat_message("assistant"):
            with st.spinner("Searching the diary…"):
                try:
                    result = _query_worker(
                        prompt,
                        worker_url=cfg["worker_url"],
                        k=cfg["k"],
                        min_score=cfg["min_score"],
                        semantic_floor=cfg["semantic_floor"],
                        synthesize=cfg["synthesize"],
                        secret=cfg["secret"],
                        model=cfg["model"],
                    )
                except httpx.ConnectError:
                    st.error(
                        f"Cannot connect to worker at **{cfg['worker_url']}**. "
                        "Is it running? (`make run`)"
                    )
                    st.session_state.messages.pop()
                    st.stop()
                except httpx.HTTPStatusError as exc:
                    st.error(
                        f"Worker returned HTTP {exc.response.status_code}: {exc.response.text}"
                    )
                    st.session_state.messages.pop()
                    st.stop()
                except WorkerError as exc:
                    st.error(f"Worker error: {exc}")
                    st.session_state.messages.pop()
                    st.stop()
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    st.error(f"Unexpected error: {exc}")
                    st.session_state.messages.pop()
                    st.stop()

            if "error" in result:
                st.error(f"Worker error: {result['error']}")
                st.session_state.messages.pop()
                st.stop()

            _render_assistant_turn(result)

        st.session_state.messages.append(
            {
                "role": "assistant",
                "content": result.get("synthesis", ""),
                "result": result,
            }
        )


if __name__ == "__main__":
    main()
