"""Image generation — local Flux2Klein via mflux (Apple Silicon only).

Used by image_server.py to keep the model loaded between requests.
Remote-server and VLM-rewrite paths live in kg_utils.synthesis.ImageSynthesizer
and kg_utils.synthesis.TextSynthesizer respectively.

Environment variables
---------------------
GUTENKG_IMAGE_MODEL   HuggingFace repo or mflux model name
                      (default: mlx-community/flux2-klein-4b-4bit)
IMAGE_STEPS           Inference steps (default: 4)
"""

from __future__ import annotations

import os
import random
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage

_DEFAULT_MODEL = "mlx-community/flux2-klein-4b-4bit"
_DEFAULT_STEPS = 4

_DEFAULT_DIMS: tuple[int, int] = (1536, 1024)


def _parse_size(size: str | None) -> tuple[int, int] | None:
    """Parse an explicit ``"WIDTHxHEIGHT"`` string into a ``(width, height)`` pair.

    :param size: Size string such as ``"768x512"`` (case-insensitive ``x``), or None.
    :returns: ``(width, height)`` when *size* parses to two positive ints, else None.
    """
    if not size:
        return None
    try:
        w_str, h_str = size.lower().split("x", 1)
        width, height = int(w_str), int(h_str)
    except (ValueError, AttributeError):
        return None
    if width <= 0 or height <= 0:
        return None
    return width, height


# Module-level model cache for image_server.py reuse
_cached_model = None
_cached_model_name: str | None = None


def _load_model(model_name: str):
    """Load Flux2Klein, reusing the cached instance when model_name is unchanged."""
    global _cached_model, _cached_model_name
    if _cached_model is not None and _cached_model_name == model_name:
        return _cached_model

    from mflux.models.flux2.variants.txt2img.flux2_klein import Flux2Klein

    _cached_model = Flux2Klein(model_path=model_name)
    _cached_model_name = model_name
    return _cached_model


def generate(
    prompt: str,
    *,
    size: str | None = None,
    seed: int | None = None,
    output_path: str | Path | None = None,
    model_name: str | None = None,
    steps: int | None = None,
) -> PILImage:
    """Generate an image locally via Flux2Klein (Apple Silicon / mflux).

    :param prompt: Text description of the image to generate.
    :param size: Explicit ``"WIDTHxHEIGHT"`` in pixels (default 1536x1024). Any
        dimensions are accepted — this replaces the old fixed aspect-ratio
        lookup, which silently snapped every request to one of seven sizes.
        Note mflux rounds each dimension DOWN to a multiple of 16 ("Width and
        height should be multiples of 16. Rounding down."), so 999x333 renders
        as 992x320. Every chat preset is already a multiple of 16 and comes
        back exactly as asked.
    :param seed: Random seed for reproducibility (random if omitted).
    :param output_path: If given, save the PNG here in addition to returning it.
    :param model_name: Override the HF model repo (default: mlx-community/flux2-klein-4b-4bit).
    :param steps: Override inference steps (default: 4).
    :returns: PIL Image.
    """
    model_name = model_name or os.environ.get("GUTENKG_IMAGE_MODEL", _DEFAULT_MODEL)
    steps = steps or int(os.environ.get("IMAGE_STEPS", _DEFAULT_STEPS))
    seed = seed if seed is not None else random.randint(0, 2**31 - 1)
    width, height = _parse_size(size) or _DEFAULT_DIMS

    model = _load_model(model_name)
    result = model.generate_image(
        seed=seed,
        prompt=prompt,
        width=width,
        height=height,
        guidance=1.0,
        num_inference_steps=steps,
        scheduler="flow_match_euler_discrete",
    )

    pil_image: PILImage = result.image

    if output_path is not None:
        pil_image.save(str(output_path))

    return pil_image
