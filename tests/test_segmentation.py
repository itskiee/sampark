"""Our segmentation model: preprocessing, tiled inference, and the shipped ONNX model."""

from __future__ import annotations

import numpy as np
import pytest

from floodmap.segmentation import available, normalise, predict_tiled
from floodmap.stages.segmentation import agreement


def test_normalise_range_and_nodata():
    vv = np.array([[-40.0, -15.0, 5.0, np.nan]])
    x = normalise(vv, vv)
    assert x.shape == (2, 1, 4) and x.dtype == np.float32
    assert x[0].tolist()[0] == pytest.approx([0.0, 0.5, 1.0, 0.0])


@pytest.mark.parametrize("shape", [(37, 53), (700, 1100), (512, 512)])
def test_tiled_inference_matches_whole_image(shape):
    """A per-pixel 'model' must give identical results tiled or whole (stitching is exact)."""
    rng = np.random.default_rng(0)
    x = rng.random((2,) + shape).astype("float32")

    def run(batch):
        assert batch.shape[2] % 8 == 0 and batch.shape[3] % 8 == 0  # U-Net size rule
        return batch[:, :1] * 0.5 + batch[:, 1:2] * 0.25

    out = predict_tiled(x, run, tile=256, overlap=16)
    np.testing.assert_allclose(out, x[0] * 0.5 + x[1] * 0.25, rtol=1e-6)


def test_agreement_counts():
    m = np.array([[1, 1, 0, 0]], dtype=bool)
    r = np.array([[1, 0, 1, 0]], dtype=bool)
    a = agreement(m, r, np.ones_like(m))
    assert a["iou"] == pytest.approx(1 / 3, abs=1e-3)
    assert a["pixels_both"] == 1 and a["pixels_model_only"] == 1


@pytest.mark.skipif(not available(), reason="model not trained yet")
def test_shipped_model_finds_a_dark_river():
    """Smoke test of the real ONNX model: a dark (water-like) strip in bright land."""
    from floodmap.segmentation import WaterModel

    rng = np.random.default_rng(1)
    vv = -8 + rng.normal(0, 1.5, (128, 160))
    vh = -15 + rng.normal(0, 1.5, (128, 160))
    vv[:, 70:90] = -22 + rng.normal(0, 1.0, (128, 20))
    vh[:, 70:90] = -29 + rng.normal(0, 1.0, (128, 20))
    prob = WaterModel().probability(vv, vh)
    assert prob[:, 74:86].mean() > 0.7
    assert np.concatenate([prob[:, :50], prob[:, 110:]], axis=1).mean() < 0.2
