"""Multi-date tracking: areas per date, receding / new water, possible lakes forming."""

from __future__ import annotations

import numpy as np

from floodmap.multidate import growing_water_bodies, series_areas, water_changes
from floodmap.thresholds import DECREASE, NO_CHANGE, NODATA, WATER

PIX_M2 = 400.0  # 20 m pixels


def _maps():
    d1 = np.full((30, 30), NO_CHANGE, dtype=np.uint8)
    d1[5:10, 5:10] = WATER  # 25 px of water that later recedes
    d1[20:22, 20:22] = WATER  # a small pond (4 px)...
    d1[0:3, 25:30] = DECREASE
    d2 = d1.copy()
    d2[5:10, 5:10] = NO_CHANGE  # receded
    d2[18:26, 18:26] = WATER  # ...that grows to 64 px = 25,600 m²: possible lake
    d2[0, 0] = NODATA
    return [d1, d2]


def test_series_and_changes():
    maps, zone = _maps(), np.ones((30, 30), bool)
    s = series_areas(maps, zone, PIX_M2 / 1e6)
    assert s[0]["water_km2"] == round(29 * PIX_M2 / 1e6, 3)
    assert s[1]["water_km2"] == round(64 * PIX_M2 / 1e6, 3)
    km2, code = water_changes(maps[0], maps[1], zone, PIX_M2 / 1e6)
    assert km2["receding_water_km2"] == round(25 * PIX_M2 / 1e6, 3)
    assert km2["new_ponding_km2"] == round(60 * PIX_M2 / 1e6, 3)
    assert km2["persistent_water_km2"] == round(4 * PIX_M2 / 1e6, 3)
    assert code[0, 0] == 0  # not observed on both dates: no claim


def test_growing_water_body_is_flagged_but_small_or_stable_ones_are_not():
    maps, zone = _maps(), np.ones((30, 30), bool)
    lakes = growing_water_bodies(maps, zone, PIX_M2, min_area_m2=20_000, growth_factor=1.5,
                                 min_later_dates=1)
    assert len(lakes) == 1 and lakes[0]["growth_factor"] == 16.0
    assert lakes[0]["area_km2_by_date"] == [round(4 * PIX_M2 / 1e6, 4), round(64 * PIX_M2 / 1e6, 4)]
    assert growing_water_bodies(maps, zone, PIX_M2, 50_000, 1.5, 1) == []
    stable = [maps[1], maps[1]]
    assert growing_water_bodies(stable, zone, PIX_M2, 20_000, 1.5, 1) == []
    outside = np.zeros((30, 30), bool)  # only the terrain-plausible zone counts
    assert growing_water_bodies(maps, outside, PIX_M2, 20_000, 1.5, 1) == []


def test_water_seen_on_one_later_image_only_is_not_a_lake():
    """Wet snow / noise: appears on the last image only -> not flagged; seen twice -> flagged."""
    d1, d_last = _maps()
    middle_without = d1.copy()  # the pond is not there on the middle date
    assert growing_water_bodies([d1, middle_without, d_last], np.ones((30, 30), bool), PIX_M2,
                                20_000, 1.5, 2) == []
    assert len(growing_water_bodies([d1, d_last, d_last], np.ones((30, 30), bool), PIX_M2,
                                    20_000, 1.5, 2)) == 1
