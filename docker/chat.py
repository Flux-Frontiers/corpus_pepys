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

import html
import io
import json
import os
from pathlib import Path

import httpx
import streamlit as st

_IN_DOCKER = os.path.exists("/.dockerenv")
_HOST = "host.docker.internal" if _IN_DOCKER else "localhost"

_DEFAULT_WORKER = os.environ.get("KGRAG_ENDPOINT", "http://localhost:8000")

_SYNTH_PROVIDERS: dict[str, str] = {
    "oMLX": "omlx",
    "Ollama": "ollama",
    "OpenAI": "openai",
}

_RESOLUTION_LABELS: dict[str, str] = {
    "Preview": "Preview  (768 × 512)",
    "Standard": "Standard  (1152 × 768)",
    "Full": "Full  (1536 × 1024)",
}

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


def _preview(text: str, n: int = 200) -> tuple[str, bool]:
    """Return (preview, truncated) — a clean ~n-char preview cut on a word boundary."""
    text = (text or "").strip()
    if len(text) <= n:
        return text, False
    cut = text[:n].rsplit(" ", 1)[0].rstrip(" ,;:—-")
    return cut + "…", True


def _render_hit_card(hit: dict) -> None:
    node_kind = hit.get("kind", "")
    name = hit.get("name", "")
    source = hit.get("source_path") or "—"
    score = float(hit.get("score", 0.0))
    # Show the real diary passage (content), falling back to the legacy summary.
    content = hit.get("content") or hit.get("summary") or ""
    preview, truncated = _preview(content, 200)

    esc_preview = html.escape(preview)
    details = ""
    if truncated:
        esc_full = html.escape(content).replace("\n", "<br>")
        details = (
            "<details style='margin-top:6px;'>"
            "<summary style='cursor:pointer;color:#D4A017;font-size:12px;'>📖 Full entry</summary>"
            f"<div style='color:#ddd;font-size:13px;margin-top:6px;line-height:1.55;'>{esc_full}</div>"
            "</details>"
        )

    st.markdown(
        f"""
        <div style="background:#1e1e2e;border-left:4px solid {_DIARY_COLOR};
                    border-radius:6px;padding:10px 14px;margin-bottom:6px;">
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
          {"<br><span style='color:#ccc;font-size:12px;'>" + esc_preview + "</span>" if esc_preview else ""}
          {details}
        </div>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Worker call
# ---------------------------------------------------------------------------


class WorkerError(Exception):
    pass


def _rewrite_via_worker(
    worker_url: str,
    text: str,
    secret: str,
    backend: str = "",
    model: str = "",
) -> tuple[str, str | None]:
    """Ask the worker to rewrite a corpus passage into an image-generation prompt."""
    payload: dict = {"input": {"op": "rewrite", "text": text}}
    if backend:
        payload["input"]["backend"] = backend
    if model:
        payload["input"]["model"] = model
    if secret:
        payload["input"]["secret"] = secret
    try:
        resp = httpx.post(
            worker_url.rstrip("/") + "/runsync",
            json=payload,
            timeout=httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=5.0),
        )
        resp.raise_for_status()
        out = resp.json().get("output", {})
        return out.get("prompt", text), out.get("error")
    except Exception as exc:  # noqa: BLE001
        return text, str(exc)


def _imagine_via_worker(
    worker_url: str,
    prompt: str,
    secret: str,
    *,
    image_backend: str = "",
    aspect_ratio: str = "3:2",
    steps: int | None = None,
) -> tuple[str | None, str | None, str | None, str | None]:
    """Route image generation through the worker. Returns (b64, image_model, image_backend, error)."""
    inp: dict[str, object] = {"op": "imagine", "prompt": prompt, "aspect_ratio": aspect_ratio}
    if image_backend:
        inp["image_backend"] = image_backend
    if steps is not None:
        inp["steps"] = steps
    if secret:
        inp["secret"] = secret
    payload = {"input": inp}
    try:
        resp = httpx.post(
            worker_url.rstrip("/") + "/runsync",
            json=payload,
            timeout=httpx.Timeout(connect=5.0, read=300.0, write=10.0, pool=5.0),
        )
        resp.raise_for_status()
        data = resp.json()
        # RunPod wraps handler errors as {"status": "FAILED", "error": "..."}
        if data.get("status") == "FAILED" or "error_type" in data:
            return None, None, None, str(data.get("error", "worker failed"))
        out = data.get("output", {})
        if "error" in out:
            return None, None, None, out["error"]
        return out.get("image_b64"), out.get("image_model"), out.get("image_backend"), None
    except Exception as exc:  # noqa: BLE001
        return None, None, None, str(exc)


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
    backend: str = "",
) -> dict:
    payload: dict = {
        "input": {
            "query": query,
            "corpus": "diary",
            "k": k,
            "min_score": min_score,
            "semantic_floor": semantic_floor,
            "synthesize": synthesize,
        }
    }
    if model:
        payload["input"]["model"] = model
    if backend:
        payload["input"]["backend"] = backend
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
        # RunPod serialises the error as a JSON string; decode it if so.
        if isinstance(error_data, str):
            try:
                error_data = json.loads(error_data)
            except (ValueError, TypeError):
                raise WorkerError(error_data) from None
        if isinstance(error_data, dict):
            err_type = error_data.get("error_type", "Unknown")
            err_msg = error_data.get("error_message", str(error_data))
            raise WorkerError(f"{err_type}: {err_msg}")
        raise WorkerError(str(error_data))

    out = data.get("output", data)
    # The worker may return a soft error in the output payload too.
    if isinstance(out, dict) and isinstance(out.get("error"), str):
        raise WorkerError(out["error"])
    return out


@st.cache_data(ttl=60, show_spinner=False)
def _fetch_models(worker_url: str, secret: str, backend: str = "") -> tuple[list[str], str]:
    """Ask the worker which synthesis models are served. Returns (model_ids, default)."""
    payload: dict = {"input": {"op": "models"}}
    if backend:
        payload["input"]["backend"] = backend
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
    except Exception:  # noqa: BLE001
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

    st.sidebar.subheader("⚙️ Search")

    k = st.sidebar.slider("Results", min_value=1, max_value=50, value=10)
    min_score = st.sidebar.slider(
        "Min score",
        min_value=0.0,
        max_value=0.9,
        value=0.5,
        step=0.05,
        help="Drop individual hits below this similarity score",
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
        help="Generate a narrative answer via the configured LLM backend",
    )

    backend = ""
    model = ""
    if synthesize:
        provider_label = st.sidebar.selectbox(
            "Provider",
            options=list(_SYNTH_PROVIDERS.keys()),
            index=0,
            help="LLM backend — oMLX (local MLX), Ollama (local), or OpenAI (cloud)",
        )
        backend = _SYNTH_PROVIDERS[provider_label]

        secret = os.environ.get("HANDLER_SECRET", "")
        with st.sidebar:
            with st.spinner("Fetching models…"):
                models, default = _fetch_models(_DEFAULT_WORKER, secret, backend)
        if models:
            default_idx = models.index(default) if default in models else 0
            model = st.sidebar.selectbox(
                "Model",
                options=models,
                index=default_idx,
                help="Model — fetched live from the selected provider",
            )
        else:
            st.sidebar.caption("⚠️ No models reported — using provider default.")
        if st.sidebar.button("🔄 Refresh models", use_container_width=True):
            _fetch_models.clear()
            st.rerun()

    st.sidebar.markdown("---")
    st.sidebar.subheader("🖼️ Image")
    resolution = st.sidebar.selectbox(
        "Resolution",
        options=list(_RESOLUTION_LABELS.keys()),
        format_func=lambda r: _RESOLUTION_LABELS[r],
        index=0,
        help="Smaller = faster generation",
    )
    aspect = st.sidebar.selectbox(
        "Aspect ratio",
        options=["3:2", "16:9", "1:1", "4:3", "9:16", "2:3"],
        index=0,
    )
    has_result = any(
        m.get("role") == "assistant" and m.get("result")
        for m in st.session_state.get("messages", [])
    )
    last_result = next(
        (
            m["result"]
            for m in reversed(st.session_state.get("messages", []))
            if m.get("role") == "assistant" and m.get("result")
        ),
        None,
    )
    if last_result:
        st.sidebar.download_button(
            "💾 Save result",
            data=_result_to_markdown(last_result),
            file_name="pepys_result.md",
            mime="text/markdown",
            use_container_width=True,
            help="Download the most recent result as Markdown",
        )
    else:
        st.sidebar.button(
            "💾 Save result",
            disabled=True,
            use_container_width=True,
            help="Run a query first",
        )
    render_clicked = st.sidebar.button(
        "🎨 Render response",
        use_container_width=True,
        disabled=not has_result,
        help="Generate an illustration from the most recent result"
        if has_result
        else "Run a query first",
    )

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
        "worker_url": _DEFAULT_WORKER,
        "secret": os.environ.get("HANDLER_SECRET", ""),
        "k": k,
        "min_score": min_score,
        "semantic_floor": semantic_floor,
        "synthesize": synthesize,
        "backend": backend,
        "model": model,
        "resolution": resolution,
        "aspect": aspect,
        "render_clicked": render_clicked,
    }


# ---------------------------------------------------------------------------
# Render one assistant turn
# ---------------------------------------------------------------------------


def _result_to_markdown(result: dict) -> str:
    """Render a query result (question, answer, sources) as a Markdown document."""
    lines = ["# Pepys Diary — Result", ""]
    if result.get("query"):
        lines += [f"**Question:** {result['query']}", ""]
    if result.get("synthesis"):
        lines += ["## Answer", "", result["synthesis"], ""]
        if result.get("model"):
            lines += [f"_Model: {result['model']}_", ""]
    hits = result.get("hits", [])
    if hits:
        lines += [f"## Source passages ({len(hits)})", ""]
        for h in hits:
            head = h.get("name") or h.get("node_id") or "passage"
            src = h.get("source_path") or "—"
            lines += [f"### {head}  ·  {src}  ·  score {h.get('score', 0):.3f}", ""]
            lines += [(h.get("content") or h.get("summary") or "").strip(), ""]
    return "\n".join(lines)


def _build_image_prompt(result: dict) -> str:
    if result.get("synthesis"):
        return result["synthesis"][:800]
    hits = result.get("hits", [])[:3]
    parts = [h.get("content") or h.get("summary") or "" for h in hits]
    return " ".join(p.strip() for p in parts if p.strip())[:800]


def _open_image(path: Path) -> None:
    st.image(str(path), use_container_width=True)
    st.caption(f"📁 {path}")


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
        model_used = result.get("model")
        if model_used:
            st.caption(f"🤖 {model_used}")
    elif synthesis_error:
        st.warning(
            f"Answer generation failed — **{synthesis_error}**\n\n"
            "Check that Ollama is running and reachable, or disable **Generate answer**."
        )
    else:
        st.info("Answer generation off — see the source passages below.")

    _parts = [f"📊 {total_hits} matching passages found"]
    if result.get("search_ms") is not None:
        _parts.append(f"search {result['search_ms']:,} ms")
    if result.get("synthesis_ms") is not None:
        _parts.append(f"synthesis {result['synthesis_ms']:,} ms")
    st.caption(" · ".join(_parts))

    with st.expander(f"📄 Source passages ({len(hits)})", expanded=not bool(synthesis)):
        for hit in hits:
            _render_hit_card(hit)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    _init_state()
    cfg = _render_sidebar()

    title_col, clear_col = st.columns([5, 1])
    with title_col:
        st.title("📔 The Diary of Samuel Pepys")
    with clear_col:
        if st.session_state.messages and st.button("🗑️ Clear", use_container_width=True):
            st.session_state.messages = []
            st.rerun()
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
                        backend=cfg["backend"],
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
                except Exception as exc:  # noqa: BLE001
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
        st.rerun()

    if cfg["render_clicked"]:
        last_result = next(
            (
                m["result"]
                for m in reversed(st.session_state.messages)
                if m.get("role") == "assistant" and m.get("result")
            ),
            None,
        )
        if last_result:
            import base64
            import tempfile
            import time

            from PIL import Image as PILImage

            st.divider()
            prompt = _build_image_prompt(last_result)
            with st.spinner("Rewriting via LLM…"):
                t0_vlm = time.perf_counter()
                prompt, vlm_error = _rewrite_via_worker(
                    cfg["worker_url"],
                    prompt,
                    cfg["secret"],
                    backend=cfg["backend"],
                    model=cfg["model"],
                )
                vlm_ms = round((time.perf_counter() - t0_vlm) * 1000)
                if vlm_error:
                    st.warning(f"Rewrite failed — sending raw corpus text. ({vlm_error})")
                else:
                    st.caption(
                        f"🎨 Prompt: {prompt[:160]}{'…' if len(prompt) > 160 else ''}"
                        f" · rewrite {vlm_ms:,} ms"
                    )
            image_backend = "openai" if cfg["backend"] == "openai" else ""
            with st.spinner("Generating image…"):
                try:
                    t0_img = time.perf_counter()
                    b64, image_model, image_backend_used, img_error = _imagine_via_worker(
                        cfg["worker_url"],
                        prompt,
                        cfg["secret"],
                        image_backend=image_backend,
                        aspect_ratio=cfg["aspect"],
                    )
                    img_ms = round((time.perf_counter() - t0_img) * 1000)
                    if img_error or not b64:
                        st.error(f"Image generation failed: {img_error or 'no image returned'}")
                    else:
                        out_path = Path(tempfile.mkdtemp()) / f"pepys_render_{int(time.time())}.png"
                        PILImage.open(io.BytesIO(base64.b64decode(b64))).save(str(out_path))
                        _open_image(out_path)
                        st.caption(
                            f"🖼️ {image_model or image_backend_used or 'unknown'}"
                            f" · {cfg['resolution']} · {cfg['aspect']} · {img_ms:,} ms"
                        )
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Image generation failed: {exc}")


if __name__ == "__main__":
    main()
