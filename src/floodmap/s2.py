"""Sentinel-2 optical change (when the sky is clear): an independent second opinion.

Optical images cannot see through cloud, so in the monsoon they are often unusable; the
radar result never depends on them. When usable they add evidence:
  new water  MNDWI = (B03 - B11) / (B03 + B11) above 0 after, not before
  mud/debris vegetation stripped: NDVI = (B08 - B04) / (B08 + B04) dropped strongly
Clouds, cloud shadows and no-data are removed with the Scene Classification Layer (SCL),
which is part of the official Sentinel-2 Level-2A product.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import rasterio

from floodmap.catalogue import S2Scene
from floodmap.s1_pairing import to_utm
from floodmap.thresholds import DECREASE, NO_CHANGE, NODATA, WATER

BANDS = ["B03", "B04", "B08", "B11", "SCL"]
SCL_CLEAR = (4, 5, 6, 7)  # vegetation, bare soil, water, unclassified (not cloud/shadow/snow)


class NoClearImageError(RuntimeError):
    """No suitable cloud-free Sentinel-2 pair; message is meant for the user."""


def choose_dates(scenes: list[S2Scene], event_date: date, aoi_geom, utm_epsg: int, *,
                 pre_window_days: int, post_window_days: int,
                 max_tile_cloud_pct: float) -> tuple[date, date, dict]:
    """Pick the clearest pre-event day and the earliest acceptable post-event day."""
    aoi_m = to_utm(aoi_geom, utm_epsg)
    by_day: dict[date, list[S2Scene]] = {}
    for s in scenes:
        by_day.setdefault(s.acquired.date(), []).append(s)

    def coverage(items):
        from shapely import union_all

        foot = to_utm(union_all([s.footprint for s in items]), utm_epsg)
        return foot.intersection(aoi_m).area / aoi_m.area if aoi_m.area else 0.0

    days = {d: (float(np.mean([s.cloud_cover for s in v])), coverage(v)) for d, v in by_day.items()}
    ok = {d: v for d, v in days.items() if v[0] <= max_tile_cloud_pct and v[1] >= 0.5}
    pre = [d for d in ok if event_date - timedelta(days=pre_window_days) <= d < event_date]
    post = [d for d in ok if event_date < d <= event_date + timedelta(days=post_window_days)]
    if not pre or not post:
        raise NoClearImageError(
            f"No Sentinel-2 image with tile cloud cover <= {max_tile_cloud_pct:g}% "
            f"{'before' if not pre else 'after'} the event within the search window "
            "(typical during the monsoon). Radar results are unaffected.")
    pre_day = min(pre, key=lambda d: (days[d][0], -d.toordinal()))  # clearest, then latest
    post_day = min(post)  # earliest acceptable after the event
    info = {"pre_day": str(pre_day), "pre_tile_cloud_pct": round(days[pre_day][0], 1),
            "post_day": str(post_day), "post_tile_cloud_pct": round(days[post_day][0], 1)}
    return pre_day, post_day, info


def fetch_pair(conn, aoi, pre_day: date, post_day: date, resolution: float, out_path):
    """One CDSE request: both days, mosaicked per day, merged into one GeoTIFF."""
    from floodmap.cdse_client import download, load_collection

    w, s, e, n = aoi.bounds
    extent = {"west": w, "south": s, "east": e, "north": n}

    def day_cube(day: date, prefix: str):
        cube = load_collection(conn, "SENTINEL2_L2A", spatial_extent=extent,
                               temporal_extent=[day.isoformat(),
                                                (day + timedelta(days=1)).isoformat()],
                               bands=BANDS)
        cube = cube.resample_spatial(resolution=resolution, projection=aoi.utm_epsg,
                                     method="near")
        cube = cube.reduce_dimension(dimension="t", reducer="first")  # tiles of one day
        return cube.rename_labels("bands", [f"{prefix}_{b}" for b in BANDS])

    merged = day_cube(pre_day, "pre").merge_cubes(day_cube(post_day, "post"))
    return download(merged, out_path)  # safe retries for busy server / broken connection


def read_pair(path) -> tuple[dict, dict, object, object]:
    names = [f"pre_{b}" for b in BANDS] + [f"post_{b}" for b in BANDS]
    with rasterio.open(path) as src:
        desc = list(src.descriptions)
        if sorted(d or "" for d in desc) != sorted(names):
            desc = names
        data = {d: src.read(i + 1).astype("float32") for i, d in enumerate(desc)}
        transform, crs = src.transform, src.crs
    pre = {b: data[f"pre_{b}"] for b in BANDS}
    post = {b: data[f"post_{b}"] for b in BANDS}
    return pre, post, transform, crs


def _ratio(a, b):
    with np.errstate(divide="ignore", invalid="ignore"):
        return (a - b) / (a + b)


def classify_s2(pre: dict, post: dict, *, water_mndwi: float, ndvi_drop: float,
                ndvi_post_max: float) -> tuple[np.ndarray, np.ndarray, dict]:
    """Classes (thresholds.py codes), clear mask, and statistics."""
    clear = (np.isin(pre["SCL"], SCL_CLEAR) & np.isin(post["SCL"], SCL_CLEAR)
             & np.isfinite(pre["B03"]) & np.isfinite(post["B03"]))
    mndwi_pre, mndwi_post = _ratio(pre["B03"], pre["B11"]), _ratio(post["B03"], post["B11"])
    ndvi_pre, ndvi_post = _ratio(pre["B08"], pre["B04"]), _ratio(post["B08"], post["B04"])
    new_water = clear & (mndwi_post > water_mndwi) & ~(mndwi_pre > water_mndwi)
    stripped = clear & ((ndvi_pre - ndvi_post) >= ndvi_drop) & (ndvi_post <= ndvi_post_max) \
        & ~new_water
    classes = np.full(clear.shape, NODATA, dtype=np.uint8)
    classes[clear] = NO_CHANGE
    classes[stripped] = DECREASE
    classes[new_water] = WATER
    stats = {"clear_share": round(float(clear.mean()), 3),
             "new_water_px": int(new_water.sum()), "vegetation_stripped_px": int(stripped.sum())}
    return classes, clear, stats
