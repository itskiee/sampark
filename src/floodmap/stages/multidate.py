"""Stage "multidate": track the flood over later Sentinel-1 passes (M8 extra).

Takes the primary track of the s1 stage, finds later passes on the SAME track (relative
orbit + pass direction) up to `max_days_after_event`, downloads each from Copernicus Data
Space, and classifies it against the SAME pre-event composite with the SAME thresholds
(floodmap/multidate.py). Reports, inside the terrain-plausible zone: area per date,
receding water, new ponding, and growing water bodies flagged as POSSIBLE lakes forming
(verify). Outputs: multidate/multidate.tif, multidate/possible_lakes.geojson,
multidate/multidate.png.
"""

from __future__ import annotations

import json
import textwrap
from datetime import UTC, datetime, time, timedelta

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import rasterio  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from rasterio.features import geometry_mask  # noqa: E402
from shapely.geometry import Point  # noqa: E402

from floodmap import attribution  # noqa: E402
from floodmap.catalogue import search_s1_scenes  # noqa: E402
from floodmap.cdse_client import connect  # noqa: E402
from floodmap.multidate import growing_water_bodies, series_areas, water_changes  # noqa: E402
from floodmap.pipeline import StageContext, register_stage  # noqa: E402
from floodmap.results import StageResult, StageStatus  # noqa: E402
from floodmap.s1_change import (  # noqa: E402
    fetch_acquisition,
    read_bands,
    speckle_filter,
    to_db,
    write_geotiff,
)
from floodmap.s1_pairing import merge_slices, to_utm, with_coverage  # noqa: E402
from floodmap.stages.s1 import threshold_params  # noqa: E402
from floodmap.thresholds import classify  # noqa: E402

VERIFY = "possible lake forming: verify (e.g. a debris dam); not confirmed"
NOT_REQUESTED = ("Not requested: tracking later satellite passes (is the water going down, is a "
                 "lake forming behind debris?) is an optional follow-up that adds about 4-8 "
                 "minutes. Switch it on with 'Track later satellite passes' in the dashboard or "
                 "--later-passes on the command line.")


def later_passes(ctx: StageContext, track: dict, post_start: datetime):
    """Later acquisitions on the same track, covering enough of the area."""
    cfg, aoi = ctx.config.multidate, ctx.aoi
    end = datetime.combine(ctx.event_date + timedelta(days=cfg.max_days_after_event + 1),
                           time(0), UTC)
    scenes = search_s1_scenes(aoi.bounds, post_start + timedelta(hours=12), end,
                              timeout_s=ctx.config.network.timeout_s,
                              retries=ctx.config.network.retries)
    same = [s for s in scenes if s.relative_orbit == track["relative_orbit"]
            and s.orbit_state == track["orbit_state"]]
    acqs = [with_coverage(a, aoi.geometry, aoi.utm_epsg)
            for a in merge_slices(same, ctx.config.s1.same_pass_minutes)]
    acqs = [a for a in acqs if a.coverage >= ctx.config.s1.min_coverage
            and a.start > post_start + timedelta(hours=12)]
    return sorted(acqs, key=lambda a: a.start)[: cfg.max_dates]


@register_stage("multidate", requires=("s1", "terrain"), uses=("confidence",))
def run_multidate(ctx: StageContext) -> StageResult:
    from floodmap.stages.confidence import _read_band_by_name

    cfg, aoi, s1cfg = ctx.config.multidate, ctx.aoi, ctx.config.s1
    if not cfg.enabled:
        return StageResult("multidate", StageStatus.SKIPPED, NOT_REQUESTED)
    s1 = ctx.results.stages["s1"].outputs
    track = s1["tracks"][0]
    pair = track["pair"]
    post_start = datetime.fromisoformat(pair["post"]["start_utc"])
    if post_start.tzinfo is None:
        post_start = post_start.replace(tzinfo=UTC)
    ctx.progress("multidate", f"searching later passes of track {pair['track']}")
    acqs = later_passes(ctx, pair, post_start)
    if not acqs:
        return StageResult("multidate", StageStatus.SKIPPED,
                           f"No later Sentinel-1 pass on track {pair['track']} within "
                           f"{cfg.max_days_after_event} days of the event yet.")

    resolution = s1["resolution_m"]
    bands = [b for b in ("VV", "VH") if b in pair["polarizations"]]
    key = {"track": pair["track"], "dates": [a.start.isoformat() for a in acqs],
           "res": resolution, "bounds": aoi.bounds, "epsg": aoi.utm_epsg}
    folder = ctx.cache.stage_dir(aoi.aoi_hash, ctx.event_date, "multidate", key)
    folder.mkdir(parents=True, exist_ok=True)
    conn = None
    with rasterio.open(track["files"]["change"]) as ref:
        pre_vv = ref.read(list(ref.descriptions).index("pre_vv_db") + 1).astype("float32")
        pre_vh = ref.read(list(ref.descriptions).index("pre_vh_db") + 1).astype("float32")
        transform, crs, shape = ref.transform, ref.crs, ref.shape
        params = threshold_params(s1cfg, resolution)
        with rasterio.open(track["files"]["classes"]) as src:
            class_maps, dates = [src.read(1)], [post_start.date().isoformat()]
        for acq in acqs:
            path = folder / f"post_{acq.start:%Y%m%dT%H%M}.tif"
            if not path.is_file():
                conn = conn or connect()
                ctx.progress("multidate", f"downloading the pass of {acq.day} (one request)")
                fetch_acquisition(conn, aoi, acq, bands, resolution, path)
            post = read_bands(path, ref)
            post_vv = to_db(speckle_filter(post["VV"], s1cfg.speckle_window_px))
            post_vh = to_db(speckle_filter(post["VH"], s1cfg.speckle_window_px)) \
                if "VH" in post else None
            valid = (np.isfinite(pre_vv) & np.isfinite(post_vv) & (pre_vv > s1cfg.min_valid_db)
                     & (post_vv > s1cfg.min_valid_db))
            lr_vv = np.where(valid, post_vv - pre_vv, np.nan).astype("float32")
            lr_vh = None if post_vh is None else np.where(
                valid & np.isfinite(pre_vh) & np.isfinite(post_vh), post_vh - pre_vh,
                np.nan).astype("float32")
            classes, _ = classify(post_vv, lr_vv, lr_vh, valid, params)
            class_maps.append(classes)
            dates.append(acq.day.isoformat())

    terr = ctx.results.stages["terrain"].outputs["files"]["terrain"]
    tag = f"track{pair['relative_orbit']}_{pair['orbit_state']}"
    zone = ~geometry_mask([to_utm(aoi.geometry, aoi.utm_epsg)], out_shape=shape,
                          transform=transform)
    zone &= _read_band_by_name(terr, "plausible", shape, transform, crs, True) > 0.5
    zone &= _read_band_by_name(terr, f"geom_ok_{tag}", shape, transform, crs, True) > 0.5
    zone &= ~(_read_band_by_name(terr, "permanent_water", shape, transform, crs, True) > 0.5)

    pixel_m2 = abs(transform.a * transform.e)
    series = series_areas(class_maps, zone, pixel_m2 / 1e6)
    changes, code = water_changes(class_maps[0], class_maps[-1], zone, pixel_m2 / 1e6)
    lakes = growing_water_bodies(class_maps, zone, pixel_m2, cfg.lake_min_area_m2,
                                 cfg.lake_growth_factor,
                                 min(cfg.lake_min_later_dates, len(class_maps) - 1))

    out = ctx.results.run_dir / "multidate"
    tif = write_geotiff(out / "multidate.tif",
                        {**{f"class_{d}": cm.astype("float32") for d, cm in
                            zip(dates, class_maps, strict=True)},
                         "water_change_code": code.astype("float32")}, transform, crs)
    back = gpd.GeoSeries([Point(transform @ (f["col"] + 0.5, f["row"] + 0.5)) for f in lakes],
                         crs=aoi.utm_epsg).to_crs(4326)
    for f, p in zip(lakes, back, strict=True):
        f.update({"lon": round(p.x, 5), "lat": round(p.y, 5), "status": VERIFY})
        f.pop("row"), f.pop("col"), f.pop("label")
    geo = out / "possible_lakes.geojson"
    if lakes:
        gpd.GeoDataFrame([{**f, "area_km2_by_date": json.dumps(f["area_km2_by_date"])}
                          for f in lakes], geometry=list(back), crs=4326).to_file(
            geo, driver="GeoJSON")
    else:
        geo.write_text('{"type": "FeatureCollection", "features": []}', encoding="utf-8")
    png = _render(ctx, class_maps, dates, code, zone, transform, series, changes, lakes,
                  out / "multidate.png")
    timeline = [{"date": d, **s} for d, s in zip(dates, series, strict=True)]
    msg = (f"{len(dates)} dates on track {pair['track']} ({dates[0]} → {dates[-1]}): water "
           f"{series[0]['water_km2']:.2f} → {series[-1]['water_km2']:.2f} km²; receding "
           f"{changes['receding_water_km2']:.2f}, new ponding {changes['new_ponding_km2']:.2f} km²"
           f"; {len(lakes)} possible lake(s) forming (verify)")
    return StageResult("multidate", StageStatus.OK, msg, outputs={
        "track": pair["track"], "timeline": timeline, "changes_first_to_last": changes,
        "possible_lakes": lakes[:20],
        "method": "later passes of the same track vs the same pre-event composite, same "
                  "thresholds, inside the terrain-plausible zone",
        "files": {"raster": tif, "lakes": geo, "png": [png]}})


def _render(ctx, class_maps, dates, code, zone, transform, series, changes, lakes, out_png):
    from floodmap.viz import CLASS_CMAP, _extent_km, crop_window

    win = crop_window(transform, code.shape, ctx.focus_bbox, ctx.aoi.utm_epsg) \
        if ctx.focus_bbox else None
    rows, cols = win or (slice(0, code.shape[0]), slice(0, code.shape[1]))
    ext = _extent_km(transform, rows, cols)
    n = len(class_maps) + 1
    fig, axes = plt.subplots(1, n, figsize=(4.2 * n, 6), constrained_layout=True)
    for ax, cm, d, s in zip(axes[:-1], class_maps, dates, series, strict=True):
        shown = np.where(zone, cm, 0)[rows, cols]
        ax.imshow(shown, cmap=CLASS_CMAP, vmin=-0.5, vmax=4.5, extent=ext,
                  interpolation="nearest")
        ax.set_title(f"{d}\nwater {s['water_km2']:.2f} km²", fontsize=9)
        ax.tick_params(labelsize=6)
    rgb = np.ones(code[rows, cols].shape + (3,))
    for c, color in ((1, (0.3, 0.6, 1.0)), (2, (0.85, 0.1, 0.1)), (3, (0.42, 0.24, 0.6))):
        rgb[code[rows, cols] == c] = color
    axes[-1].imshow(rgb, extent=ext, interpolation="nearest")
    axes[-1].set_title(f"{dates[0]} → {dates[-1]}", fontsize=9)
    axes[-1].legend(handles=[Patch(color=(0.3, 0.6, 1.0), label="receding water"),
                             Patch(color=(0.85, 0.1, 0.1), label="new ponding"),
                             Patch(color=(0.42, 0.24, 0.6), label="persistent water")],
                    loc="lower right", fontsize=7)
    sub = (f"Receding {changes['receding_water_km2']:.2f} km² · new ponding "
           f"{changes['new_ponding_km2']:.2f} km² · persistent "
           f"{changes['persistent_water_km2']:.2f} km² · possible lakes forming: {len(lakes)} "
           "(verify). Same track, same pre-event composite, same thresholds; terrain-plausible "
           "zone only.")
    fig.suptitle(f"Multi-date tracking (Sentinel-1) — {ctx.results.event_name}\n"
                 + "\n".join(textwrap.wrap(sub, 150)), fontsize=10)
    footer = "\n".join(textwrap.wrap(attribution.footer_text(), 190))
    fig.text(0.5, -0.02, footer, ha="center", va="top", fontsize=7, color="#444444")
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=110, bbox_inches="tight")
    plt.close(fig)
    return out_png
