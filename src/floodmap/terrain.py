"""Copernicus DEM terrain analysis: slope, HAND, radar layover/shadow, flow paths.

Why terrain matters for flood mapping in mountains:
- Water and debris settle in LOW ground NEAR rivers. HAND (Height Above Nearest Drainage)
  = how many metres a pixel sits above the stream it drains into. A "flooded" pixel 300 m
  above the river is a false alarm (vegetation change, wet snow, radar artefact).
- Steep slopes do not hold standing water or thick deposits.
- Radar artefacts: a slope facing the satellite more steeply than the radar's incidence
  angle is folded over ("layover"); a slope facing away more steeply than 90° minus the
  incidence angle is never illuminated ("shadow"). Change detected there is not reliable.
All inputs come from the Copernicus DEM (GLO-30) via Copernicus Data Space.
"""

from __future__ import annotations

import logging
import math
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import Resampling, reproject

from floodmap.aoi import AOI
from floodmap.cdse_client import download, load_collection
from floodmap.s1_pairing import to_utm

log = logging.getLogger("floodmap.terrain")

# pysheds' default D8 direction codes: N, NE, E, SE, S, SW, W, NW
DIRMAP = (64, 128, 1, 2, 4, 8, 16, 32)
# Row/column step for each D8 code (rows grow southwards)
D8_STEP = {64: (-1, 0), 128: (-1, 1), 1: (0, 1), 2: (1, 1), 4: (1, 0), 8: (1, -1),
           16: (0, -1), 32: (-1, -1)}

# Sentinel-1 is right-looking. Approximate ground-track heading at low/mid latitudes:
# ascending ≈ 347° (flying north, slightly west), descending ≈ 193°. The radar looks 90°
# to the right of the heading: ascending → ~77° (east-north-east), descending → ~283°.
LOOK_AZIMUTH_DEG = {"ascending": 77.0, "descending": 283.0}
DEFAULT_INCIDENCE_DEG = 39.0  # middle of the IW swath (29°–46°)


def buffered_bounds(aoi: AOI, buffer_m: float) -> tuple[float, float, float, float]:
    """WGS84 bbox of the AOI enlarged by `buffer_m` metres (for hydrology context)."""
    from pyproj import Transformer

    minx, miny, maxx, maxy = to_utm(aoi.geometry, aoi.utm_epsg).bounds
    back = Transformer.from_crs(aoi.utm_epsg, 4326, always_xy=True)
    xs, ys = back.transform([minx - buffer_m, maxx + buffer_m, minx - buffer_m, maxx + buffer_m],
                            [miny - buffer_m, miny - buffer_m, maxy + buffer_m, maxy + buffer_m])
    return min(xs), min(ys), max(xs), max(ys)


def fetch_dem(conn, aoi: AOI, buffer_m: float, resolution: float, out_path: Path) -> Path:
    """Copernicus DEM GLO-30 for the buffered AOI, in the AOI's UTM grid, from CDSE."""
    w, s, e, n = buffered_bounds(aoi, buffer_m)
    cube = load_collection(conn, "COPERNICUS_30",
                           spatial_extent={"west": w, "south": s, "east": e, "north": n})
    cube = cube.max_time().resample_spatial(resolution=resolution, projection=aoi.utm_epsg,
                                            method="bilinear")
    return download(cube, out_path)  # safe retries for busy server / broken connection


def slope_and_gradients(dem: np.ndarray, res: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Slope (degrees) and elevation gradients dz/dx (east) and dz/dy (north)."""
    dz_drow, dz_dcol = np.gradient(dem.astype("float64"), res)
    dz_dx = dz_dcol
    dz_dy = -dz_drow  # rows grow southwards, so north = -row
    slope = np.degrees(np.arctan(np.hypot(dz_dx, dz_dy)))
    return slope.astype("float32"), dz_dx, dz_dy


def layover_shadow(dz_dx: np.ndarray, dz_dy: np.ndarray, look_azimuth_deg: float,
                   incidence_deg: float) -> tuple[np.ndarray, np.ndarray]:
    """Active layover and shadow masks from terrain slope along the radar look direction.

    s = elevation change per metre moving AWAY from the satellite (along the look direction).
    s > 0: slope faces the satellite; layover where atan(s) > incidence angle.
    s < 0: slope faces away; shadow where atan(-s) > 90° - incidence angle.
    (Shadow cast by distant ridges is not modelled; documented limitation.)
    """
    az = math.radians(look_azimuth_deg)
    s = dz_dx * math.sin(az) + dz_dy * math.cos(az)
    angle = np.degrees(np.arctan(s))
    layover = angle > incidence_deg
    shadow = -angle > (90.0 - incidence_deg)
    return layover, shadow


@dataclass
class Hydrology:
    fdir: np.ndarray  # D8 codes (DIRMAP)
    acc: np.ndarray  # upstream cell count
    hand: np.ndarray  # metres above nearest drainage
    streams: np.ndarray  # bool


def _numpy_compat_for_pysheds() -> None:
    """pysheds 0.5 still calls numpy.in1d, which numpy 2.4+ removed in favour of numpy.isin.
    pysheds only calls it on flattened 1-D arrays, where both are identical, so we provide the
    old name as an alias instead of pinning an older numpy for the whole project."""
    if not hasattr(np, "in1d"):
        np.in1d = lambda ar1, ar2, assume_unique=False, invert=False: np.isin(  # type: ignore[attr-defined]
            np.ravel(ar1), ar2, assume_unique=assume_unique, invert=invert)


def hydrology(dem_path: Path, stream_area_km2: float) -> Hydrology:
    """Flow direction, accumulation, streams and HAND with pysheds (pit/flat handling)."""
    _numpy_compat_for_pysheds()
    from pysheds.grid import Grid

    with rasterio.open(dem_path) as src:
        dem = src.read(1).astype("float32")
        transform, crs = src.transform, src.crs
        cell_area_km2 = abs(src.res[0] * src.res[1]) / 1e6
    if not np.isfinite(dem).all():  # pysheds needs a gap-free surface
        dem = np.where(np.isfinite(dem), dem, np.nanmin(dem))
    with tempfile.TemporaryDirectory() as tmp:
        clean = Path(tmp) / "dem.tif"
        with rasterio.open(clean, "w", driver="GTiff", height=dem.shape[0], width=dem.shape[1],
                           count=1, dtype="float32", crs=crs, transform=transform,
                           nodata=-9999.0) as dst:
            dst.write(dem, 1)
        grid = Grid.from_raster(str(clean))
        raster = grid.read_raster(str(clean))
        filled = grid.fill_depressions(grid.fill_pits(raster))
        inflated = grid.resolve_flats(filled)
        fdir = grid.flowdir(inflated, dirmap=DIRMAP)
        acc = grid.accumulation(fdir, dirmap=DIRMAP)
        streams = acc >= stream_area_km2 / cell_area_km2  # stays a pysheds Raster
        hand = grid.compute_hand(fdir, raster, streams, dirmap=DIRMAP)
    hand_arr = np.asarray(hand, dtype="float32")
    hand_arr[~np.isfinite(hand_arr)] = np.nan
    return Hydrology(np.asarray(fdir).astype("int32"), np.asarray(acc).astype("float32"),
                     hand_arr, np.asarray(streams, dtype=bool))


def trace_flow_path(fdir: np.ndarray, acc: np.ndarray, start_rc: tuple[int, int],
                    snap_radius_px: int, max_steps: int = 200_000) -> list[tuple[int, int]]:
    """Follow D8 flow directions downstream from a start cell.

    The start is first "snapped" to the cell with the largest upstream area within
    `snap_radius_px`, so a click near (not exactly on) the river starts on the channel.
    Stops at the grid edge, at a sink, or if a loop is detected.
    """
    rows, cols = fdir.shape
    r0, c0 = start_rc
    r_lo, r_hi = max(r0 - snap_radius_px, 0), min(r0 + snap_radius_px + 1, rows)
    c_lo, c_hi = max(c0 - snap_radius_px, 0), min(c0 + snap_radius_px + 1, cols)
    window = acc[r_lo:r_hi, c_lo:c_hi]
    dr, dc = np.unravel_index(int(np.nanargmax(window)), window.shape)
    r, c = r_lo + int(dr), c_lo + int(dc)

    path = [(r, c)]
    seen = {(r, c)}
    for _ in range(max_steps):
        step = D8_STEP.get(int(fdir[r, c]))
        if step is None:  # sink / no-data
            break
        r, c = r + step[0], c + step[1]
        if not (0 <= r < rows and 0 <= c < cols) or (r, c) in seen:
            break
        path.append((r, c))
        seen.add((r, c))
    return path


def path_lengths_m(path: list[tuple[int, int]], res: float) -> np.ndarray:
    """Cumulative distance (m) along a D8 path (diagonal steps count √2 · res)."""
    if len(path) < 2:
        return np.zeros(len(path))
    steps = np.diff(np.asarray(path), axis=0)
    seg = np.where(np.abs(steps).sum(axis=1) == 2, math.sqrt(2) * res, res)
    return np.concatenate([[0.0], np.cumsum(seg)])


def to_grid(src_arr: np.ndarray, src_transform, dst_shape, dst_transform, crs,
            categorical: bool) -> np.ndarray:
    """Resample a DEM-grid array onto the radar grid (same CRS)."""
    dtype = "float32" if not categorical else src_arr.dtype
    dst = np.zeros(dst_shape, dtype=dtype) if categorical else np.full(dst_shape, np.nan,
                                                                        dtype="float32")
    reproject(src_arr.astype(dtype), dst, src_transform=src_transform, src_crs=crs,
              dst_transform=dst_transform, dst_crs=crs,
              resampling=Resampling.nearest if categorical else Resampling.bilinear)
    return dst
