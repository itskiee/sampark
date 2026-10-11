from __future__ import annotations

import numpy as np
import pytest

from floodmap.thresholds import (
    DECREASE,
    INCREASE,
    NO_CHANGE,
    NODATA,
    WATER,
    ThresholdParams,
    class_areas_km2,
    classify,
    otsu_threshold,
    remove_small_patches,
)

P = ThresholdParams(method="fixed", decrease_db=-3.0, increase_db=3.0, water_post_vv_db=-15.0,
                    otsu_decrease_bounds_db=(-10.0, -1.5), otsu_increase_bounds_db=(1.5, 10.0),
                    otsu_min_separability=0.5, min_mapping_unit_px=5)


def synthetic_scene(seed: int = 0):
    """200x200 scene: noise around 0 dB change, plus planted water / mud / debris patches."""
    rng = np.random.default_rng(seed)
    lr = rng.normal(0.0, 0.8, (200, 200))       # speckle-level change everywhere
    post = rng.normal(-8.0, 1.5, (200, 200))    # typical land backscatter
    lr[20:40, 20:60] = -8.0                      # new water: big drop...
    post[20:40, 20:60] = -20.0                   # ...and dark afterwards
    lr[80:100, 20:60] = -6.0                     # wet mud: drop, but not dark
    post[80:100, 20:60] = -11.0
    lr[140:170, 100:150] = 6.0                   # debris: strong increase
    valid = np.ones((200, 200), bool)
    valid[:, 190:] = False                       # image border
    return post, lr, valid


def test_planted_patches_are_classified_correctly():
    post, lr, valid = synthetic_scene()
    classes, info = classify(post, lr, None, valid, P)
    assert (classes[25:35, 25:55] == WATER).all()
    assert (classes[85:95, 25:55] == DECREASE).all()
    assert (classes[145:165, 105:145] == INCREASE).all()
    assert (classes[:, 190:] == NODATA).all()
    background = classes[100:140, 160:185]
    assert (background == NO_CHANGE).mean() > 0.99  # noise is not flagged
    assert info["decrease_method"] == "fixed"


def test_isolated_noise_pixels_are_removed_by_min_mapping_unit():
    post, lr, valid = synthetic_scene()
    lr[5, 5] = -9.0  # single extreme pixel
    post[5, 5] = -22.0
    classes, _ = classify(post, lr, None, valid, P)
    assert classes[5, 5] == NO_CHANGE


def test_vh_adds_increase_where_vv_is_quiet():
    post, lr, valid = synthetic_scene()
    lr[60:70, 120:140] = 0.0  # VV shows nothing here...
    lr_vh = np.zeros_like(lr)
    lr_vh[60:70, 120:140] = 5.0  # ...but VH sees a strong increase
    classes, _ = classify(post, lr, lr_vh, valid, P)
    assert (classes[62:68, 122:138] == INCREASE).all()


def test_vh_drop_counts_as_decrease_p16():
    post, lr, valid = synthetic_scene()
    lr[110:125, 150:180] = 0.0  # VV quiet...
    lr_vh = np.zeros_like(lr)
    lr_vh[110:125, 150:180] = -6.0  # ...VH drops strongly (e.g. flooded vegetated bank)
    post[110:125, 150:180] = -10.0  # not water-dark in VV
    classes, _ = classify(post, lr, lr_vh, valid, P)
    assert (classes[112:123, 152:178] == DECREASE).all()
    post[110:125, 150:180] = -19.0  # dark in VV -> water
    classes, _ = classify(post, lr, lr_vh, valid, P)
    assert (classes[112:123, 152:178] == WATER).all()


def test_conflicting_vv_increase_and_vh_drop_is_decrease():
    post, lr, valid = synthetic_scene()
    lr[110:125, 150:180] = 5.0
    lr_vh = np.zeros_like(lr)
    lr_vh[110:125, 150:180] = -5.0
    classes, _ = classify(post, lr, lr_vh, valid, P)
    assert not (classes[112:123, 152:178] == INCREASE).any()


def test_otsu_on_bimodal_tail_is_used_and_bounded():
    rng = np.random.default_rng(1)
    vals = np.concatenate([rng.normal(-0.5, 0.3, 50_000), rng.normal(-7.0, 0.5, 20_000)])
    t, sep = otsu_threshold(vals)
    assert -6.0 < t < -1.5 and sep > 0.7


def test_otsu_unimodal_falls_back_to_fixed():
    post, lr, valid = synthetic_scene()
    lr = np.random.default_rng(2).normal(0, 0.8, lr.shape)  # nothing changed at all
    classes, info = classify(post, lr, None, valid, ThresholdParams(**{**P.__dict__,
                                                                         "method": "otsu"}))
    assert info["decrease_method"].startswith("fixed")
    assert info["increase_method"].startswith("fixed")
    assert (classes[valid] == NO_CHANGE).mean() > 0.99  # no-flood case stays clean


def test_otsu_handles_degenerate_input():
    assert otsu_threshold(np.array([1.0, 1.0, 1.0]))[1] == 0.0
    assert otsu_threshold(np.full(1000, 2.0))[1] == 0.0


def test_remove_small_patches():
    m = np.zeros((10, 10), bool)
    m[0, 0] = True
    m[5:8, 5:8] = True
    out = remove_small_patches(m, 5)
    assert not out[0, 0] and out[5:8, 5:8].all()


def test_class_areas():
    classes = np.array([[NO_CHANGE, WATER], [DECREASE, INCREASE]], dtype=np.uint8)
    areas = class_areas_km2(classes, pixel_area_m2=400.0)
    assert areas["new water-like"] == pytest.approx(0.0004, abs=1e-6)
    inside = np.array([[True, False], [True, True]])
    assert class_areas_km2(classes, 400.0, inside)["new water-like"] == 0.0
