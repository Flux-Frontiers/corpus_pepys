# © 2026 Eric G. Suchanek, PhD — Flux-Frontiers · SPDX-License-Identifier: Elastic-2.0
"""
sdxl_server.py — corpus_pepys SDXL-Lightning image server (diffusers)

A portable alternative to ``image_server.py`` (mflux / FLUX.2). It exposes the
identical OpenAI-style ``/v1/images/generations`` contract, so the worker only
needs ``IMAGE_ENDPOINT`` repointed — no worker or chat changes.

Why this exists
---------------
``image_server.py`` runs FLUX.2 through mflux, whose own metadata is
``mlx ; sys_platform == "darwin"`` and ``mlx[cuda13] ; sys_platform == "linux"``
— Apple Silicon, or a Linux box with an NVIDIA GPU, and no Windows wheel at all.
This one resolves ``cuda -> mps -> cpu``, so it runs anywhere: fast on a GPU,
usable on Apple Silicon, slow but working on plain CPU. Backed by SDXL base plus
a ByteDance SDXL-Lightning UNet, so it generates in 2/4/8 steps.

Usage
-----
    make sdxl-server        # creates .venv-sdxl, fetches weights, serves :8091
    make up IMAGE_BACKEND=sdxl

`make up` selects this automatically on any host that cannot run mflux.

Environment variables
---------------------
SDXL_MODEL        Lightning variant: sdxl_lightning_2 | _4 (default) | _8
SDXL_BASE         SDXL base repo (default: stabilityai/stable-diffusion-xl-base-1.0)
SDXL_OFFLINE      1 = never reach HuggingFace; fail if weights are not cached
MPS_DTYPE         float16 (default; uses the fp16-fix VAE) | float32 (heavier)
IMAGE_OUTPUT_DIR  Dir for response_format=filepath (default: /tmp/pepys_images)
SDXL_SERVER_HOST  Bind host (default: 0.0.0.0)
SDXL_SERVER_PORT  Bind port (default: 8091)

Ported from gutenberg_kg's ``serve/sdxl_server.py``. Two deliberate changes,
both worth porting back: torch is imported lazily (below), and the weights are
fetched on first run rather than being required to be already cached.
"""

from __future__ import annotations

import asyncio
import base64
import os
import time
import uuid
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

if TYPE_CHECKING:  # torch is only present in .venv-sdxl; see _torch() below
    import torch

app = FastAPI(title="corpus_pepys SDXL-Lightning image server")

_MODEL = os.environ.get("SDXL_MODEL", "sdxl_lightning_4")
_BASE = os.environ.get("SDXL_BASE", "stabilityai/stable-diffusion-xl-base-1.0")
_OUTPUT_DIR = Path(os.environ.get("IMAGE_OUTPUT_DIR", "/tmp/pepys_images"))
_LIGHTNING_REPO = "ByteDance/SDXL-Lightning"

# fp16-safe VAE: the stock SDXL VAE overflows in float16 and yields black
# images; this fixed one lets the whole pipeline run float16 (~half the memory).
_VAE_FP16_FIX = "madebyollin/sdxl-vae-fp16-fix"

# SDXL-Lightning enforces the step count it was trained for; guidance is ~1.0.
_STEPS: dict[str, int] = {"sdxl_lightning_2": 2, "sdxl_lightning_4": 4, "sdxl_lightning_8": 8}

_DEFAULT_DIMS: tuple[int, int] = (1024, 1024)

_pipe: Any = None  # module-level pipeline cache


def _offline() -> bool:
    """Whether to refuse network access when resolving weights.

    :returns: True when ``SDXL_OFFLINE`` is set to a truthy value.
    """
    return os.environ.get("SDXL_OFFLINE", "").strip().lower() in {"1", "true", "yes"}


def _torch():
    """Import torch on demand.

    Deferred so this module can be imported — for tests, docs, or a quick
    ``--help`` — from an environment without the diffusers stack. Only actually
    rendering needs it. ``image_gen.py`` defers its mflux import for the same
    reason; gutenberg_kg's copy of this file imports torch at module scope,
    which is why it cannot be imported outside ``.venv-sdxl``.

    :returns: The imported :mod:`torch` module.
    :raises RuntimeError: When the isolated environment has not been built.
    """
    try:
        import torch as _t
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "The SDXL backend needs the isolated diffusers environment. "
            "Run `make sdxl-server`, which creates .venv-sdxl from "
            "docker/requirements-sdxl.txt, rather than launching this module "
            "from the project env."
        ) from exc
    return _t


def _parse_size(size: str | None) -> tuple[int, int]:
    """Parse ``"WIDTHxHEIGHT"`` into a ``(width, height)`` pair.

    :param size: Size string such as ``"1024x1024"`` (case-insensitive ``x``).
    :returns: The parsed pair, or ``_DEFAULT_DIMS`` when it does not parse to
              two positive ints.
    """
    if not size:
        return _DEFAULT_DIMS
    try:
        w_str, h_str = size.lower().split("x", 1)
        width, height = int(w_str), int(h_str)
    except (ValueError, AttributeError):
        return _DEFAULT_DIMS
    if width <= 0 or height <= 0:
        return _DEFAULT_DIMS
    return width, height


def _steps_for(model: str, requested: int | None = None) -> int:
    """Resolve the inference step count.

    SDXL-Lightning UNets are distilled for a fixed step count, so the model name
    wins over any per-request override — asking a 4-step UNet for 30 steps does
    not improve it, it degrades it.

    :param model: Lightning variant name.
    :param requested: Per-request override, used only for an unknown variant.
    :returns: Step count to run.
    """
    if model in _STEPS:
        return _STEPS[model]
    return requested if requested and requested > 0 else 4


def _device_dtype() -> tuple[str, torch.dtype]:
    """Resolve the ``(device, dtype)`` to run on.

    Prefers CUDA, then Apple MPS, then CPU — the fallback chain that makes this
    server portable where the mflux one is not. MPS defaults to float16, which
    is safe because the fp16-fix VAE is swapped in; set ``MPS_DTYPE=float32``
    for the heavier full-precision path.

    :returns: Tuple of device string and torch dtype.
    """
    torch = _torch()
    if torch.cuda.is_available():
        return "cuda", torch.float16
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        want = os.environ.get("MPS_DTYPE", "float16").strip().lower()
        return "mps", (torch.float32 if want == "float32" else torch.float16)
    return "cpu", torch.float32


def _load_pipeline():
    """Build the SDXL + Lightning-UNet pipeline once and cache it.

    Weights are fetched from HuggingFace on first run (several GB) unless
    ``SDXL_OFFLINE`` is set, in which case only an existing local cache is used
    and a miss raises. gutenberg_kg's copy is hard-wired to local-files-only,
    so a fresh machine fails with a cache-miss error rather than downloading.

    :returns: The cached diffusers pipeline.
    """
    global _pipe
    if _pipe is not None:
        return _pipe

    torch = _torch()
    # diffusers lives only in .venv-sdxl, so it is imported here rather than at
    # module scope — same reason as torch above.
    try:
        from diffusers import AutoencoderKL
        from diffusers.pipelines.stable_diffusion_xl.pipeline_stable_diffusion_xl import (
            StableDiffusionXLPipeline,
        )
        from diffusers.schedulers.scheduling_euler_discrete import EulerDiscreteScheduler
        from huggingface_hub import hf_hub_download
        from safetensors.torch import load_file
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "The SDXL backend needs the isolated diffusers environment. "
            "Run `make sdxl-server`, which creates .venv-sdxl from "
            "docker/requirements-sdxl.txt."
        ) from exc

    device, dtype = _device_dtype()
    steps = _steps_for(_MODEL)
    local_only = _offline()
    if not local_only:
        print("[startup] weights are fetched on first run and cached (~7 GB).", flush=True)
    print(f"[startup] loading {_BASE} + {_MODEL} on {device}/{dtype} ...", flush=True)

    pipe = StableDiffusionXLPipeline.from_pretrained(
        _BASE, torch_dtype=dtype, local_files_only=local_only
    ).to(device)

    if dtype == torch.float16:
        print(f"[startup] loading fp16-fix VAE {_VAE_FP16_FIX} ...", flush=True)
        pipe.vae = AutoencoderKL.from_pretrained(
            _VAE_FP16_FIX, torch_dtype=dtype, local_files_only=local_only
        ).to(device)

    unet_path = hf_hub_download(
        _LIGHTNING_REPO,
        f"sdxl_lightning_{steps}step_unet.safetensors",
        local_files_only=local_only,
    )
    state = load_file(unet_path, device=device)
    missing, unexpected = pipe.unet.load_state_dict(state, strict=False)
    if missing or unexpected:
        print(
            f"[startup] UNet load: missing={len(missing)} unexpected={len(unexpected)}",
            flush=True,
        )

    # Lightning wants Euler with trailing timestep spacing.
    pipe.scheduler = EulerDiscreteScheduler.from_config(
        pipe.scheduler.config, timestep_spacing="trailing"
    )
    try:
        pipe.enable_attention_slicing()
        pipe.vae.enable_slicing()
    except (AttributeError, RuntimeError, NotImplementedError):
        pass

    _pipe = pipe
    print("[startup] pipeline ready", flush=True)
    return _pipe


class ImageGenRequest(BaseModel):
    """OpenAI-shaped image generation request — matches ``image_server.py``."""

    model: str = "sdxl_lightning_4"
    prompt: str
    n: int = 1
    size: str = "1024x1024"
    quality: str | None = None
    num_inference_steps: int | None = None
    seed: int | None = None
    response_format: str = "b64_json"
    negative_prompt: str = "blurry, bad quality, distorted"


@app.get("/v1/models")
def list_models() -> dict:
    """OpenAI-compatible single-model listing.

    :returns: A one-entry model list naming the active Lightning variant.
    """
    return {
        "object": "list",
        "data": [{"id": _MODEL, "object": "model", "owned_by": "sdxl-lightning"}],
    }


def _render(req: ImageGenRequest):
    """Run a blocking SDXL-Lightning render.

    :param req: The parsed generation request.
    :returns: A PIL image.
    """
    torch = _torch()
    width, height = _parse_size(req.size)
    pipe = _load_pipeline()
    steps = _steps_for(_MODEL, req.num_inference_steps)
    device, _ = _device_dtype()

    generator = None
    if req.seed is not None:
        try:
            generator = torch.Generator(device=device).manual_seed(int(req.seed))
        except (RuntimeError, TypeError):
            generator = None

    result = pipe(
        prompt=req.prompt,
        negative_prompt=req.negative_prompt,
        width=width,
        height=height,
        num_inference_steps=steps,
        guidance_scale=1.0,  # Lightning is distilled without CFG
        generator=generator,
    )
    image = result.images[0]

    # Return the caching allocator's freed blocks so idle memory drops back to
    # baseline between renders.
    if device == "mps":
        torch.mps.empty_cache()
    elif device == "cuda":
        torch.cuda.empty_cache()
    return image


@app.post("/v1/images/generations")
async def generate_image(req: ImageGenRequest) -> JSONResponse:
    """Generate an image and return it as base64 or a saved file path.

    :param req: The generation request.
    :returns: OpenAI-shaped JSON carrying ``b64_json`` or ``filepath``.
    """
    loop = asyncio.get_event_loop()
    pil = await loop.run_in_executor(None, lambda: _render(req))

    if req.response_format == "filepath":
        _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        out = _OUTPUT_DIR / f"{uuid.uuid4().hex}.png"
        pil.save(str(out))
        return JSONResponse({"created": int(time.time()), "data": [{"filepath": str(out)}]})

    buf = BytesIO()
    pil.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return JSONResponse({"created": int(time.time()), "data": [{"b64_json": b64}]})


def main() -> None:
    """Run the server, preloading the pipeline so the first request is not slow."""
    # Imported here rather than at module scope: uvicorn is only needed to
    # actually serve, so importing this module for tests or docs does not
    # require it.
    import uvicorn

    _load_pipeline()
    host = os.environ.get("SDXL_SERVER_HOST", "0.0.0.0")
    port = int(os.environ.get("SDXL_SERVER_PORT", "8091"))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
