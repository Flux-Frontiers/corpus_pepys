"""Unit tests for docker/image_gen.py — size parsing and generate() logic.

mflux (Apple Silicon) is only imported inside _load_model, so the module can
be imported and tested without the full image stack.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import image_gen  # docker/ is on sys.path via conftest

# The resolution presets docker/chat.py sends. All three must reach the model
# unchanged: they previously snapped to a fixed aspect-ratio table, and since
# only 1536x1024 appeared in it, every preset rendered at full size.
_CHAT_PRESETS = ["768x512", "1152x768", "1536x1024"]


# ---------------------------------------------------------------------------
# _parse_size
# ---------------------------------------------------------------------------


class TestParseSize:
    def test_parses_plain_size(self):
        assert image_gen._parse_size("768x512") == (768, 512)

    def test_parses_uppercase_x(self):
        assert image_gen._parse_size("1536X1024") == (1536, 1024)

    def test_accepts_arbitrary_dimensions(self):
        # The point of the change: any size, not one of seven fixed ratios.
        assert image_gen._parse_size("999x333") == (999, 333)

    def test_none_returns_none(self):
        assert image_gen._parse_size(None) is None

    def test_empty_string_returns_none(self):
        assert image_gen._parse_size("") is None

    def test_malformed_returns_none(self):
        for bad in ("1536", "1536x", "x1024", "wide", "1536x1024x8", "12.5x10"):
            assert image_gen._parse_size(bad) is None, f"{bad!r} should not parse"

    def test_non_positive_returns_none(self):
        for bad in ("0x512", "768x0", "-768x512"):
            assert image_gen._parse_size(bad) is None, f"{bad!r} should not parse"

    def test_default_dims_are_two_positive_ints(self):
        w, h = image_gen._DEFAULT_DIMS
        assert isinstance(w, int) and w > 0
        assert isinstance(h, int) and h > 0


# ---------------------------------------------------------------------------
# _load_model — cache hit path (no mflux import required)
# ---------------------------------------------------------------------------


class TestLoadModelCache:
    def test_cached_model_returned_for_same_name(self):
        sentinel = MagicMock()
        image_gen._cached_model = sentinel
        image_gen._cached_model_name = "test-model"
        try:
            result = image_gen._load_model("test-model")
            assert result is sentinel
        finally:
            image_gen._cached_model = None
            image_gen._cached_model_name = None

    def test_different_name_bypasses_cache(self):
        sentinel = MagicMock()
        new_model = MagicMock()
        image_gen._cached_model = sentinel
        image_gen._cached_model_name = "old-model"

        flux2_klein = MagicMock(return_value=new_model)
        klein_mod = MagicMock(Flux2Klein=flux2_klein)

        import sys

        sys.modules["mflux"] = MagicMock()
        sys.modules["mflux.models"] = MagicMock()
        sys.modules["mflux.models.flux2"] = MagicMock()
        sys.modules["mflux.models.flux2.variants"] = MagicMock()
        sys.modules["mflux.models.flux2.variants.txt2img"] = MagicMock()
        sys.modules["mflux.models.flux2.variants.txt2img.flux2_klein"] = klein_mod

        try:
            result = image_gen._load_model("new-model")
            assert result is not sentinel
        finally:
            image_gen._cached_model = None
            image_gen._cached_model_name = None


# ---------------------------------------------------------------------------
# generate()
# ---------------------------------------------------------------------------


def _setup_mock_model():
    mock_pil = MagicMock()
    mock_result = MagicMock()
    mock_result.image = mock_pil
    mock_model = MagicMock()
    mock_model.generate_image.return_value = mock_result
    return mock_pil, mock_model


class TestGenerate:
    def test_returns_pil_image(self):
        mock_pil, mock_model = _setup_mock_model()
        with patch.object(image_gen, "_load_model", return_value=mock_model):
            result = image_gen.generate("a peaceful river", seed=42)
        assert result is mock_pil

    def test_default_size_when_omitted(self):
        _, mock_model = _setup_mock_model()
        with patch.object(image_gen, "_load_model", return_value=mock_model):
            image_gen.generate("test", seed=1)
        w, h = image_gen._DEFAULT_DIMS
        kwargs = mock_model.generate_image.call_args.kwargs
        assert kwargs["width"] == w
        assert kwargs["height"] == h

    def test_explicit_size_reaches_model_unchanged(self):
        _, mock_model = _setup_mock_model()
        with patch.object(image_gen, "_load_model", return_value=mock_model):
            image_gen.generate("portrait", size="1024x1536", seed=1)
        kwargs = mock_model.generate_image.call_args.kwargs
        assert (kwargs["width"], kwargs["height"]) == (1024, 1536)

    def test_arbitrary_size_not_snapped_to_a_ratio(self):
        # Regression: 999x333 matches no known aspect ratio. It used to fall
        # back to 3:2 and render at 1536x1024.
        _, mock_model = _setup_mock_model()
        with patch.object(image_gen, "_load_model", return_value=mock_model):
            image_gen.generate("test", size="999x333", seed=1)
        kwargs = mock_model.generate_image.call_args.kwargs
        assert (kwargs["width"], kwargs["height"]) == (999, 333)

    def test_malformed_size_falls_back_to_default(self):
        _, mock_model = _setup_mock_model()
        with patch.object(image_gen, "_load_model", return_value=mock_model):
            image_gen.generate("test", size="not-a-size", seed=1)
        kwargs = mock_model.generate_image.call_args.kwargs
        assert (kwargs["width"], kwargs["height"]) == image_gen._DEFAULT_DIMS

    def test_explicit_seed_forwarded_to_model(self):
        _, mock_model = _setup_mock_model()
        with patch.object(image_gen, "_load_model", return_value=mock_model):
            image_gen.generate("test", seed=12345)
        assert mock_model.generate_image.call_args.kwargs["seed"] == 12345

    def test_none_seed_replaced_with_random(self):
        _, mock_model = _setup_mock_model()
        with patch.object(image_gen, "_load_model", return_value=mock_model):
            image_gen.generate("test")  # seed=None → random
        seed_used = mock_model.generate_image.call_args.kwargs["seed"]
        assert isinstance(seed_used, int)
        assert 0 <= seed_used < 2**31

    def test_saves_to_output_path(self, tmp_path):
        mock_pil, mock_model = _setup_mock_model()
        out = tmp_path / "out.png"
        with patch.object(image_gen, "_load_model", return_value=mock_model):
            image_gen.generate("test", seed=1, output_path=out)
        mock_pil.save.assert_called_once_with(str(out))

    def test_no_save_when_output_path_none(self):
        mock_pil, mock_model = _setup_mock_model()
        with patch.object(image_gen, "_load_model", return_value=mock_model):
            image_gen.generate("test", seed=1, output_path=None)
        mock_pil.save.assert_not_called()

    def test_steps_override_forwarded(self):
        _, mock_model = _setup_mock_model()
        with patch.object(image_gen, "_load_model", return_value=mock_model):
            image_gen.generate("test", seed=1, steps=20)
        assert mock_model.generate_image.call_args.kwargs["num_inference_steps"] == 20

    def test_every_chat_preset_reaches_the_model_verbatim(self):
        # The actual bug: Preview and Standard both rendered at 1536x1024
        # because neither appeared in the aspect-ratio map.
        for preset in _CHAT_PRESETS:
            expected = tuple(int(v) for v in preset.split("x"))
            _, mock_model = _setup_mock_model()
            with patch.object(image_gen, "_load_model", return_value=mock_model):
                image_gen.generate("test", size=preset, seed=1)
            kwargs = mock_model.generate_image.call_args.kwargs
            got = (kwargs["width"], kwargs["height"])
            assert got == expected, f"{preset}: rendered at {got}, expected {expected}"
