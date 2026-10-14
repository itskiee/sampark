from __future__ import annotations

import math

import numpy as np
import pytest

from sampark.terrain import (
    D8_STEP,
    layover_shadow,
    path_lengths_m,
    slope_and_gradients,
    trace_flow_path,
)


def test_slope_of_a_plane():
    res = 30.0
    rows, cols = np.mgrid[0:50, 0:50]
    dem = cols * res * math.tan(math.radians(20))  # rises 20° towards the east
    slope, dz_dx, dz_dy = slope_and_gradients(dem, res)
    assert slope[25, 25] == pytest.approx(20.0, abs=0.01)
    assert dz_dx[25, 25] > 0 and abs(dz_dy[25, 25]) < 1e-9


def test_north_facing_gradient_sign():
    rows, _ = np.mgrid[0:50, 0:50]
    dem = -rows * 10.0  # elevation increases towards the north (smaller row index)
    _, _, dz_dy = slope_and_gradients(dem, 30.0)
    assert dz_dy[25, 25] > 0


def _slope_gradients(angle_deg: float, facing: str):
    """Gradient of a plane rising `angle_deg` towards the east ('east') or west ('west')."""
    g = math.tan(math.radians(angle_deg)) * (1 if facing == "east" else -1)
    return np.full((5, 5), g), np.zeros((5, 5))


def test_layover_when_slope_faces_radar_steeper_than_incidence():
    # Radar looking east: moving away from the satellite the ground rises, so the slope
    # faces the satellite. 50° > 39° incidence angle -> layover.
    dz_dx, dz_dy = _slope_gradients(50, "east")
    layover, shadow = layover_shadow(dz_dx, dz_dy, look_azimuth_deg=90, incidence_deg=39)
    assert layover.all() and not shadow.any()


def test_shadow_when_slope_faces_away_steeper_than_complement():
    dz_dx, dz_dy = _slope_gradients(60, "west")  # ground drops moving away from the radar
    layover, shadow = layover_shadow(dz_dx, dz_dy, look_azimuth_deg=90, incidence_deg=39)
    assert shadow.all() and not layover.any()  # 60° > 90° - 39° = 51°


def test_gentle_slopes_are_fine():
    dz_dx, dz_dy = _slope_gradients(20, "east")
    layover, shadow = layover_shadow(dz_dx, dz_dy, 90, 39)
    assert not layover.any() and not shadow.any()


def test_trace_flow_path_follows_directions_and_snaps():
    fdir = np.full((10, 10), 4, dtype=np.int32)  # everything flows south
    fdir[:, 5] = 4
    acc = np.ones((10, 10), dtype=np.float32)
    acc[:, 5] = 100  # the "river" column
    path = trace_flow_path(fdir, acc, start_rc=(0, 4), snap_radius_px=1)
    assert path[0] == (0, 5)  # snapped onto the river
    assert path[-1] == (9, 5) and len(path) == 10


def test_trace_stops_on_loop_and_sink():
    fdir = np.zeros((3, 3), dtype=np.int32)  # 0 = sink everywhere
    acc = np.ones((3, 3), dtype=np.float32)
    assert trace_flow_path(fdir, acc, (1, 1), 0) == [(1, 1)]
    fdir = np.array([[1, 4, 0], [64, 16, 0], [0, 0, 0]], dtype=np.int32)  # 2x2 loop
    path = trace_flow_path(fdir, acc, (0, 0), 0)
    assert len(path) == 4


def test_hydrology_on_synthetic_valley(tmp_path):
    """Real pysheds run: a V-shaped valley draining south. The valley floor must be a
    stream with HAND ~0, the ridges must have large HAND, flow must go downhill."""
    import rasterio
    from rasterio.transform import from_origin

    from sampark.terrain import hydrology

    res = 30.0
    rows, cols = np.mgrid[0:80, 0:60]
    dem = (np.abs(cols - 30) * 15.0 + (80 - rows) * 5.0 + 500).astype("float32")
    path = tmp_path / "dem.tif"
    with rasterio.open(path, "w", driver="GTiff", height=80, width=60, count=1, dtype="float32",
                       crs="EPSG:32645", transform=from_origin(330000, 3120000, res, res)) as dst:
        dst.write(dem, 1)
    h = hydrology(path, stream_area_km2=0.5)
    assert h.streams[60, 30] and not h.streams[60, 5]
    assert h.hand[60, 30] == pytest.approx(0.0, abs=1.0)
    assert h.hand[60, 5] > 300  # 25 cells × 15 m above the valley floor
    assert D8_STEP[int(h.fdir[40, 30])][0] == 1  # valley floor flows south (row +1)


def test_path_lengths_count_diagonals():
    path = [(0, 0), (0, 1), (1, 2)]
    lengths = path_lengths_m(path, 30.0)
    assert lengths[-1] == pytest.approx(30 + 30 * math.sqrt(2))
    assert set(D8_STEP) == {1, 2, 4, 8, 16, 32, 64, 128}
