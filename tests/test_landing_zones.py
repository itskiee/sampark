"""Landing-zone candidates: flat, dry, clear of buildings, big enough; nearest selection."""

from __future__ import annotations

import numpy as np

from floodmap.landing_zones import candidates, nearest_for, suitable_mask

RES = 20.0


def _world():
    slope = np.full((40, 40), 2.0)
    slope[:, 30:] = 25.0  # steep slope on the right
    final = np.ones((40, 40), dtype=np.uint8)  # no change
    final[:, 12:15] = 3  # a debris strip
    buildings = np.zeros((40, 40), bool)
    buildings[30:40, 0:5] = True  # a village in the lower-left corner
    return slope, final, buildings


def test_suitable_excludes_steep_flooded_and_buildings():
    slope, final, buildings = _world()
    ok = suitable_mask(slope, final, buildings, 6)
    assert not ok[:, 30:].any()  # steep
    assert not ok[:, 12:15].any()  # debris
    assert not ok[30:, :5].any()  # buildings
    assert ok[5, 5] and ok[5, 20]


def test_candidates_need_a_clear_square_and_sit_in_the_middle():
    slope, final, buildings = _world()
    ok = suitable_mask(slope, final, buildings, 6)
    found = candidates(ok, RES, 60)  # 3 x 3 pixels
    assert len(found) == 2  # the two dry flat patches either side of the debris strip
    for r, c, side in found:
        assert ok[r - 1:r + 2, c - 1:c + 2].all() and side >= 60
    assert candidates(np.zeros((10, 10), bool), RES, 60) == []
    narrow = np.zeros((20, 20), bool)
    narrow[:, 5:7] = True  # only 40 m wide
    assert candidates(narrow, RES, 60) == []


def test_nearest_within_distance():
    picks = nearest_for(np.array([[0.0, 0.0], [10_000.0, 0.0]]),
                        np.array([[100.0, 0.0], [2500.0, 0.0], [9000.0, 0.0]]), 3000, 2)
    assert [i for i, _ in picks[0]] == [0, 1]
    assert [i for i, _ in picks[1]] == [2]  # 1 km away; the others are beyond 3 km
    assert nearest_for(np.array([[0.0, 0.0]]), np.zeros((0, 2)), 3000, 2) == [[]]


def test_building_mask_in_batches_equals_one_call():
    """Batching (memory fix for dense cities, 2026-10-14) must give exactly the same mask."""
    import numpy as np
    import shapely
    from rasterio.features import rasterize
    from rasterio.transform import from_origin

    from floodmap.stages.landing_zones import near_buildings_mask

    rng = np.random.default_rng(5)
    geoms = np.array([shapely.box(x, y, x + 15, y + 12) for x, y in rng.uniform(0, 4000, (500, 2))]
                     + [shapely.Point(x, y) for x, y in rng.uniform(0, 4000, (200, 2))],
                     dtype=object)
    transform, shape = from_origin(0, 4000, 20, 20), (200, 200)
    import geopandas as gpd

    # the ORIGINAL method (geopandas buffer, one call): the 2026-10-14 Trishuli comparison
    # found 747 -> 746 sites when shapely's default circle resolution was used instead
    one = rasterize(list(gpd.GeoSeries(geoms).buffer(30)), out_shape=shape,
                    transform=transform).astype(bool)
    batched = near_buildings_mask(geoms, 30, shape, transform, batch=37)
    assert one.sum() > 1000 and np.array_equal(one, batched)
