from __future__ import annotations

from datetime import UTC, date, datetime

import numpy as np
import pytest
from shapely.geometry import box

from floodmap.catalogue import S2Scene
from floodmap.s2 import NoClearImageError, choose_dates, classify_s2
from floodmap.thresholds import DECREASE, NO_CHANGE, NODATA, WATER

AOI = box(85.28, 28.10, 85.42, 28.30)
FULL = box(84.5, 27.5, 86.5, 29.0)
KW = dict(pre_window_days=60, post_window_days=30, max_tile_cloud_pct=70)


def sc(day: str, cloud: float, foot=FULL):
    return S2Scene(day, datetime.fromisoformat(f"{day}T05:00:00+00:00").astimezone(UTC),
                   cloud, foot)


def test_choose_clearest_pre_and_earliest_post():
    scenes = [sc("2026-07-10", 20), sc("2026-08-04", 10), sc("2026-08-20", 90),
              sc("2026-08-29", 95), sc("2026-09-03", 60), sc("2026-09-08", 5)]
    pre, post, info = choose_dates(scenes, date(2026, 8, 26), AOI, 32645, **KW)
    assert pre == date(2026, 8, 4) and post == date(2026, 9, 3)
    assert info["post_tile_cloud_pct"] == 60


def test_monsoon_all_cloudy_gives_clear_message():
    scenes = [sc("2026-08-04", 99), sc("2026-08-29", 98)]
    with pytest.raises(NoClearImageError, match="monsoon"):
        choose_dates(scenes, date(2026, 8, 26), AOI, 32645, **KW)


def test_partial_footprints_are_ignored():
    sliver = box(85.41, 28.10, 85.50, 28.30)
    scenes = [sc("2026-08-04", 5, sliver), sc("2026-08-29", 5)]
    with pytest.raises(NoClearImageError):
        choose_dates(scenes, date(2026, 8, 26), AOI, 32645, **KW)


def _bands(green, red, nir, swir, scl, shape=(20, 20)):
    return {"B03": np.full(shape, green, "float32"), "B04": np.full(shape, red, "float32"),
            "B08": np.full(shape, nir, "float32"), "B11": np.full(shape, swir, "float32"),
            "SCL": np.full(shape, scl, "float32")}


def test_classify_new_water_stripped_vegetation_and_clouds():
    pre = _bands(0.06, 0.04, 0.35, 0.15, 4)  # vegetation
    post = _bands(0.06, 0.04, 0.35, 0.15, 4)
    post["B03"][0:5, :], post["B11"][0:5, :] = 0.10, 0.03  # became water (MNDWI > 0)
    post["B04"][10:15, :], post["B08"][10:15, :] = 0.15, 0.18  # vegetation stripped (mud)
    post["SCL"][18:, :] = 9  # cloud
    classes, clear, stats = classify_s2(pre, post, water_mndwi=0.0, ndvi_drop=0.25,
                                        ndvi_post_max=0.35)
    assert (classes[0:5, :] == WATER).all()
    assert (classes[10:15, :] == DECREASE).all()
    assert (classes[18:, :] == NODATA).all()
    assert (classes[6:9, :] == NO_CHANGE).all()
    assert stats["clear_share"] == 0.9
