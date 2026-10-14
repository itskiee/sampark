from __future__ import annotations

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from sampark.aoi import parse_aoi
from sampark.s1_change import (
    analysis_resolution,
    change_layers,
    load_composite,
    load_separate,
    speckle_filter,
    to_db,
    write_geotiff,
)
from sampark.thresholds import WATER
from sampark.viz import crop_window, render_s1

TRANSFORM = from_origin(330000, 3120000, 20, 20)
CRS = rasterio.crs.CRS.from_epsg(32645)


def _write(path, bands: dict):
    write_geotiff(path, bands, TRANSFORM, CRS)
    return path


def _image(value_db: float, shape=(60, 80), noise=0.0, seed=0):
    rng = np.random.default_rng(seed)
    lin = 10 ** (value_db / 10) * (1 + noise * rng.standard_normal(shape))
    return np.clip(lin, 1e-6, None).astype("float32")


def test_to_db_and_invalid_values():
    out = to_db(np.array([1.0, 0.1, 0.0, -1.0, np.nan], dtype="float32"))
    assert out[0] == pytest.approx(0.0) and out[1] == pytest.approx(-10.0)
    assert np.isnan(out[2:]).all()


def test_speckle_filter_keeps_nodata_and_reduces_noise():
    img = _image(-8, noise=0.5)
    img[:, :5] = np.nan
    out = speckle_filter(img, 3)
    assert np.isnan(out[:, :5]).all()
    assert np.nanstd(out[:, 10:]) < np.nanstd(img[:, 10:])
    assert speckle_filter(img, 1) is img  # window 1 = filter switched off


def test_change_layers_log_ratio_and_validity():
    pre = {"VV": _image(-8), "VH": _image(-15)}
    post = {"VV": _image(-8), "VH": _image(-15)}
    post["VV"][10:20, 10:20] = 10 ** (-20 / 10)  # became water: -8 -> -20 dB
    post["VV"][30:35, :] = 10 ** (-35 / 10)      # below the -30 dB validity floor
    layers = change_layers(pre, post, TRANSFORM, CRS, speckle_window=3, min_valid_db=-30,
                           n_pre=1)
    assert layers.lr_vv[14, 14] == pytest.approx(-12.0, abs=0.01)
    assert layers.lr_vv[45, 45] == pytest.approx(0.0, abs=0.01)
    assert not layers.valid[31:34, 5:75].any()
    assert layers.lr_vh is not None


def test_vv_only_images_work():
    layers = change_layers({"VV": _image(-8)}, {"VV": _image(-9)}, TRANSFORM, CRS, 3, -30, 2)
    assert layers.lr_vh is None and layers.valid.all()


def test_load_composite_named_and_unnamed_bands(tmp_path):
    bands = {"pre_VV": _image(-8), "pre_VH": _image(-15), "post_VV": _image(-20),
             "post_VH": _image(-25)}
    pre, post, transform, crs = load_composite(_write(tmp_path / "c.tif", bands), ["VV", "VH"])
    assert to_db(post["VV"])[0, 0] == pytest.approx(-20, abs=0.01)
    assert crs == CRS and transform == TRANSFORM

    unnamed = tmp_path / "u.tif"
    with rasterio.open(unnamed, "w", driver="GTiff", height=60, width=80, count=4,
                       dtype="float32", crs=CRS, transform=TRANSFORM) as dst:
        for i, arr in enumerate(bands.values(), start=1):
            dst.write(arr, i)
    pre, post, _, _ = load_composite(unnamed, ["VV", "VH"])
    assert to_db(pre["VH"])[0, 0] == pytest.approx(-15, abs=0.01)


def test_load_separate_takes_median_of_pre_images(tmp_path):
    post = _write(tmp_path / "post.tif", {"VV": _image(-10), "VH": _image(-17)})
    pres = [_write(tmp_path / f"pre_{i}.tif", {"VV": _image(v), "VH": _image(-16)})
            for i, v in enumerate((-6, -8, -30))]  # one outlier
    pre, _, _, _ = load_separate(post, pres)
    assert to_db(pre["VV"])[5, 5] == pytest.approx(-8, abs=0.01)  # median ignores outlier


def test_analysis_resolution_scales_with_area():
    small = parse_aoi([85.28, 28.10, 85.42, 28.30], 2500, 0.5)
    big = parse_aoi([85.10, 27.85, 85.45, 28.32], 2500, 0.5)
    assert analysis_resolution(small, 20, 16_000_000) == 20
    assert analysis_resolution(big, 20, 1_000_000) > 20


def test_render_and_crop_smoke(tmp_path):
    pre_db = to_db(_image(-8, noise=0.3))
    post_db = pre_db.copy()
    post_db[10:20, 10:30] = -20
    lr = post_db - pre_db
    classes = np.ones(pre_db.shape, dtype=np.uint8)
    classes[10:20, 10:30] = WATER
    png = render_s1(pre_db, post_db, lr, classes, TRANSFORM, "test", "subtitle",
                    tmp_path / "x.png")
    assert png.stat().st_size > 10_000
    win = crop_window(TRANSFORM, classes.shape, (85.0, 28.0, 86.0, 29.0), 32645)
    assert win is not None
    assert crop_window(TRANSFORM, classes.shape, (10.0, 10.0, 10.1, 10.1), 32645) is None
