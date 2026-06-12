"""Unit tests for docker/image_gen.py — aspect-ratio table and generate() logic.

mflux (Apple Silicon) is only imported inside _load_model, so the module can
be imported and tested without the full image stack.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import image_gen  # docker/ is on sys.path via conftest

_DOCUMENTED_RATIOS = ["1:1", "3:2", "2:3", "16:9", "9:16", "4:3", "3:4"]


# ---------------------------------------------------------------------------
# _ASPECT_SIZES
# ---------------------------------------------------------------------------


class TestAspectSizes:
    def test_contains_all_documented_ratios(self):
        for ratio in _DOCUMENTED_RATIOS:
            assert ratio in image_gen._ASPECT_SIZES, f"missing ratio {ratio!r}"

    def test_no_undocumented_ratios(self):
        assert set(image_gen._ASPECT_SIZES) == set(_DOCUMENTED_RATIOS)

    def test_all_values_are_two_positive_int_tuples(self):
        for ratio, size in image_gen._ASPECT_SIZES.items():
            assert isinstance(size, tuple) and len(size) == 2, f"{ratio}: not a 2-tuple"
            w, h = size
            assert isinstance(w, int) and w > 0, f"{ratio}: width invalid"
            assert isinstance(h, int) and h > 0, f"{ratio}: height invalid"

    def test_square_1_1_equal_dimensions(self):
        w, h = image_gen._ASPECT_SIZES["1:1"]
        assert w == h

    def test_landscape_3_2_wider_than_tall(self):
        w, h = image_gen._ASPECT_SIZES["3:2"]
        assert w > h

    def test_portrait_2_3_taller_than_wide(self):
        w, h = image_gen._ASPECT_SIZES["2:3"]
        assert h > w

    def test_widescreen_16_9_wider_than_tall(self):
        w, h = image_gen._ASPECT_SIZES["16:9"]
        assert w > h

    def test_vertical_9_16_taller_than_wide(self):
        w, h = image_gen._ASPECT_SIZES["9:16"]
        assert h > w

    def test_landscape_4_3_wider_than_tall(self):
        w, h = image_gen._ASPECT_SIZES["4:3"]
        assert w > h

    def test_portrait_3_4_taller_than_wide(self):
        w, h = image_gen._ASPECT_SIZES["3:4"]
        assert h > w


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

    def test_default_aspect_ratio_3_2(self):
        _, mock_model = _setup_mock_model()
        with patch.object(image_gen, "_load_model", return_value=mock_model):
            image_gen.generate("test", seed=1)
        w, h = image_gen._ASPECT_SIZES["3:2"]
        kwargs = mock_model.generate_image.call_args.kwargs
        assert kwargs["width"] == w
        assert kwargs["height"] == h

    def test_custom_aspect_ratio_portrait(self):
        _, mock_model = _setup_mock_model()
        with patch.object(image_gen, "_load_model", return_value=mock_model):
            image_gen.generate("portrait", aspect_ratio="2:3", seed=1)
        w, h = image_gen._ASPECT_SIZES["2:3"]
        kwargs = mock_model.generate_image.call_args.kwargs
        assert kwargs["width"] == w
        assert kwargs["height"] == h

    def test_unknown_aspect_ratio_falls_back_to_3_2(self):
        _, mock_model = _setup_mock_model()
        with patch.object(image_gen, "_load_model", return_value=mock_model):
            image_gen.generate("test", aspect_ratio="99:1", seed=1)
        w, h = image_gen._ASPECT_SIZES["3:2"]
        kwargs = mock_model.generate_image.call_args.kwargs
        assert kwargs["width"] == w
        assert kwargs["height"] == h

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

    def test_all_ratios_produce_valid_dimensions(self):
        for ratio in _DOCUMENTED_RATIOS:
            _, mock_model = _setup_mock_model()
            with patch.object(image_gen, "_load_model", return_value=mock_model):
                image_gen.generate("test", aspect_ratio=ratio, seed=1)
            w, h = image_gen._ASPECT_SIZES[ratio]
            kwargs = mock_model.generate_image.call_args.kwargs
            assert kwargs["width"] == w and kwargs["height"] == h, f"wrong dims for {ratio!r}"
