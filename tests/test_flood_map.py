from __future__ import annotations

import numpy as np

from sampark.flood_map import (
    CONF_HIGH,
    CONF_LOW,
    CONF_MEDIUM,
    MASKED,
    PERMANENT_WATER,
    Source,
    area_table,
    combine,
)
from sampark.thresholds import DECREASE, INCREASE, NO_CHANGE, NODATA, WATER

SHAPE = (40, 40)


def _src(name, patches, usable=None):
    cls = np.full(SHAPE, NO_CHANGE, dtype=np.uint8)
    for (r0, r1, c0, c1), code in patches:
        cls[r0:r1, c0:c1] = code
    return Source(name, cls, np.ones(SHAPE, bool) if usable is None else usable)


def _run(sources, plausible=None, perm=None, nb=1, min_patch=10):
    plausible = np.ones(SHAPE, bool) if plausible is None else plausible
    perm = np.zeros(SHAPE, bool) if perm is None else perm
    return combine(sources, plausible, perm, nb, min_patch)


def test_two_agreeing_tracks_give_high_confidence():
    a = _src("asc", [((5, 10, 5, 10), WATER)])
    b = _src("desc", [((5, 10, 6, 11), DECREASE)])  # shifted by one pixel
    final, conf, agree = _run([a, b])
    assert final[7, 7] == WATER  # water wins over decrease
    assert conf[7, 7] == CONF_HIGH and agree[7, 7] == 2


def test_single_source_medium_or_low_by_patch_size():
    a = _src("asc", [((20, 30, 20, 30), INCREASE), ((2, 3, 30, 32), INCREASE)])
    final, conf, _ = _run([a, _src("desc", [])], min_patch=50)
    assert conf[25, 25] == CONF_MEDIUM  # 100-pixel patch
    assert conf[2, 30] == CONF_LOW  # 2-pixel patch


def test_detection_outside_plausible_zone_is_masked():
    plausible = np.ones(SHAPE, bool)
    plausible[:, 20:] = False  # e.g. high above the river
    a = _src("asc", [((5, 10, 25, 30), INCREASE)])
    final, conf, _ = _run([a], plausible=plausible)
    assert (final[5:10, 25:30] == MASKED).all() and conf[5:10, 25:30].max() == 0


def test_layover_in_one_track_is_ignored_for_that_track():
    usable_b = np.ones(SHAPE, bool)
    usable_b[0:20, :] = False  # track b sees nothing reliable here
    a = _src("asc", [])
    b = _src("desc", [((5, 10, 5, 10), INCREASE)], usable=usable_b)
    final, conf, _ = _run([a, b])
    assert (final[5:10, 5:10] == NO_CHANGE).all()  # a observed it and saw no change


def test_permanent_water_is_not_reported_as_flood():
    perm = np.zeros(SHAPE, bool)
    perm[15:18, :] = True
    a = _src("asc", [((14, 19, 0, 40), DECREASE)])
    final, _, _ = _run([a], perm=perm)
    assert (final[15:18, :] == PERMANENT_WATER).all()
    assert (final[14, :] == DECREASE).all()


def test_nodata_where_no_source_observes():
    usable = np.zeros(SHAPE, bool)
    final, _, _ = _run([_src("asc", [], usable=usable)])
    assert (final == NODATA).all()


def test_area_table():
    a = _src("asc", [((0, 10, 0, 10), WATER)])
    b = _src("desc", [((0, 10, 0, 10), WATER)])
    final, conf, _ = _run([a, b])
    t = area_table(final, conf, pixel_area_m2=400.0)
    assert t["by_class_km2"]["new water-like"] == 0.04
    assert t["affected_by_confidence_km2"]["high"] == 0.04
    assert t["affected_total_km2"] == 0.04
    assert CONF_LOW < CONF_MEDIUM < CONF_HIGH
