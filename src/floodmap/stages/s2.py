"""Stage "s2": Sentinel-2 optical change when cloud-free (extra evidence, never required)."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

from floodmap.catalogue import search_s2_scenes
from floodmap.cdse_client import connect
from floodmap.pipeline import StageContext, register_stage
from floodmap.results import StageResult, StageStatus
from floodmap.s1_change import analysis_resolution, write_geotiff
from floodmap.s2 import NoClearImageError, choose_dates, classify_s2, fetch_pair, read_pair


@register_stage("s2")
def run_s2(ctx: StageContext) -> StageResult:
    cfg, aoi, event = ctx.config.s2, ctx.aoi, ctx.event_date
    start = datetime.combine(event - timedelta(days=cfg.pre_window_days), time(0), UTC)
    end = datetime.combine(event + timedelta(days=cfg.post_window_days + 1), time(0), UTC)
    ctx.progress("s2", "searching the Copernicus Data Space catalogue for Sentinel-2 images")
    scenes = search_s2_scenes(aoi.bounds, start, end, timeout_s=ctx.config.network.timeout_s,
                              retries=ctx.config.network.retries)
    try:
        pre_day, post_day, info = choose_dates(
            scenes, event, aoi.geometry, aoi.utm_epsg, pre_window_days=cfg.pre_window_days,
            post_window_days=cfg.post_window_days, max_tile_cloud_pct=cfg.max_tile_cloud_pct)
    except NoClearImageError as exc:
        return StageResult("s2", StageStatus.SKIPPED, str(exc),
                           outputs={"scenes_found": len(scenes)})

    resolution = analysis_resolution(aoi, ctx.config.s1.resolution_m, ctx.config.s1.max_pixels)
    key = {"pre": info["pre_day"], "post": info["post_day"], "res": resolution,
           "epsg": aoi.utm_epsg, "bounds": aoi.bounds}
    folder = ctx.cache.stage_dir(aoi.aoi_hash, event, "s2_download", key)
    path = folder / "s2_pair.tif"
    if not ctx.cache.is_complete(folder):
        folder.mkdir(parents=True, exist_ok=True)
        ctx.progress("s2", f"CDSE: Sentinel-2 L2A {pre_day} and {post_day} (one request)")
        fetch_pair(connect(), aoi, pre_day, post_day, resolution, path)
        ctx.cache.mark_complete(folder, key)
    pre, post, transform, crs = read_pair(path)
    classes, clear, stats = classify_s2(pre, post, water_mndwi=cfg.water_mndwi,
                                        ndvi_drop=cfg.ndvi_drop, ndvi_post_max=cfg.ndvi_post_max)
    info.update(stats)
    if stats["clear_share"] < cfg.min_clear_share:
        return StageResult("s2", StageStatus.SKIPPED,
                           f"Sentinel-2 too cloudy: only {stats['clear_share']:.0%} of the area "
                           f"is clear in both {pre_day} and {post_day}. Radar results are "
                           "unaffected.", outputs=info)
    tif = write_geotiff(ctx.results.run_dir / "s2" / "s2_classes.tif", {"class": classes},
                        transform, crs)
    px_km2 = resolution**2 / 1e6
    return StageResult(
        "s2", StageStatus.OK if stats["clear_share"] >= 0.5 else StageStatus.PARTIAL,
        f"Sentinel-2 {pre_day} → {post_day}: {stats['clear_share']:.0%} clear; new water "
        f"{stats['new_water_px'] * px_km2:.2f} km², vegetation stripped "
        f"{stats['vegetation_stripped_px'] * px_km2:.2f} km²",
        outputs={**info, "files": {"classes": tif}},
        warnings=[] if stats["clear_share"] >= 0.5 else
        [f"only {stats['clear_share']:.0%} of the area is cloud-free in both images"],
    )
