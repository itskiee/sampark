"""Stage "s1": Sentinel-1 change detection (where did the flood / debris hit?).

1. find same-track before/after images in the CDSE catalogue (s1_pairing)
2. take the best track and, if available, the best track of the OPPOSITE pass direction
   (ascending vs descending: they view the valley from opposite sides)
3. per track: download calibrated + terrain-corrected images (cached), speckle filter,
   log-ratio, classify water / wet mud / debris change
4. write per-track GeoTIFFs + PNG quick-looks of the primary track
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, time, timedelta

import numpy as np
from rasterio.features import geometry_mask

from floodmap.catalogue import search_s1_scenes
from floodmap.cdse_client import connect
from floodmap.pipeline import StageContext, register_stage
from floodmap.results import StageResult, StageStatus
from floodmap.s1_change import (
    analysis_resolution,
    change_layers,
    fetch_pair_composite,
    fetch_pair_separately,
    load_composite,
    load_separate,
    write_geotiff,
)
from floodmap.s1_pairing import NoPairError, TrackPair, rank_pairs, to_utm
from floodmap.thresholds import (
    CLASS_NAMES,
    INCREASE,
    WATER,
    ThresholdParams,
    class_areas_km2,
    classify,
)
from floodmap.viz import crop_window, render_s1

log = logging.getLogger("floodmap.stages.s1")


def threshold_params(cfg, resolution: float) -> ThresholdParams:
    return ThresholdParams(
        method=cfg.threshold_method,
        decrease_db=cfg.decrease_db,
        increase_db=cfg.increase_db,
        water_post_vv_db=cfg.water_post_vv_db,
        otsu_decrease_bounds_db=tuple(cfg.otsu_decrease_bounds_db),
        otsu_increase_bounds_db=tuple(cfg.otsu_increase_bounds_db),
        otsu_min_separability=cfg.otsu_min_separability,
        min_mapping_unit_px=max(1, round(cfg.min_mapping_unit_m2 / resolution**2)),
    )


def select_tracks(pairs: list[TrackPair], max_tracks: int) -> list[TrackPair]:
    """Best pair, plus the best pair with the opposite pass direction (if wanted)."""
    chosen = [pairs[0]]
    if max_tracks >= 2:
        other = next((p for p in pairs[1:] if p.orbit_state != pairs[0].orbit_state), None)
        if other is not None:
            chosen.append(other)
    return chosen


def _download(ctx: StageContext, pair: TrackPair, bands, resolution, warnings) -> tuple:
    aoi, event = ctx.aoi, ctx.event_date
    key = {
        "post": pair.post.scene_ids, "pre": [p.scene_ids for p in pair.pre],
        "bands": bands, "resolution": resolution, "epsg": aoi.utm_epsg,
        "bounds": aoi.bounds, "processing": "sar_backscatter sigma0-ellipsoid COPERNICUS_30",
    }
    folder = ctx.cache.stage_dir(aoi.aoi_hash, event, "s1_download", key)
    composite = folder / "pair_composite.tif"
    if ctx.cache.is_complete(folder):
        ctx.progress("s1", f"track {pair.relative_orbit}: using cached images")
        mode, cached = ("composite" if composite.exists() else "separate"), True
    else:
        cached = False
        folder.mkdir(parents=True, exist_ok=True)  # finished files from a failed run are reused
        conn = connect()
        try:
            ctx.progress("s1", f"track {pair.relative_orbit} {pair.orbit_state}: CDSE calibrates "
                               f"{len(pair.pre) + 1} images with Copernicus DEM terrain "
                               "correction (one request, a few minutes)")
            fetch_pair_composite(conn, aoi, pair, bands, resolution, composite)
            mode = "composite"
        except Exception as exc:
            log.warning("One-request S1 download failed, falling back to per-image requests",
                        exc_info=True)
            warnings.append(f"track {pair.relative_orbit}: one-request download failed "
                            f"({type(exc).__name__}: {str(exc)[:150]}); used per-image requests")
            fetch_pair_separately(conn, aoi, pair, bands, resolution, folder, ctx.progress)
            mode = "separate"
        ctx.cache.mark_complete(folder, {"pair": pair.describe(), "mode": mode})
    if mode == "composite":
        return load_composite(composite, bands) + (mode, cached)
    pre_paths = [folder / f"pre_{i + 1}.tif" for i in range(len(pair.pre))]
    return load_separate(folder / "post.tif", pre_paths) + (mode, cached)


def _process_track(ctx: StageContext, pair: TrackPair, resolution: float, primary: bool,
                   warnings: list[str]) -> dict:
    cfg, aoi = ctx.config.s1, ctx.aoi
    bands = [b for b in ("VV", "VH") if b in pair.polarizations]
    pre, post, transform, crs, mode, cached = _download(ctx, pair, bands, resolution, warnings)
    layers = change_layers(pre, post, transform, crs, cfg.speckle_window_px, cfg.min_valid_db,
                           len(pair.pre))
    if not layers.valid.any():
        raise ValueError(f"track {pair.relative_orbit}: no valid radar pixels in this area")

    params = threshold_params(cfg, resolution)
    classes, th = classify(layers.post_vv_db, layers.lr_vv, layers.lr_vh, layers.valid, params)
    inside = ~geometry_mask([to_utm(aoi.geometry, aoi.utm_epsg)], out_shape=classes.shape,
                            transform=transform)
    areas = class_areas_km2(classes, resolution**2, inside)
    valid_share = float(layers.valid[inside].mean()) if inside.any() else 0.0
    if valid_share < 0.9:
        warnings.append(f"track {pair.relative_orbit}: only {valid_share:.0%} of the area has "
                        "valid radar data (image edges / radar shadow)")

    tag = f"track{pair.relative_orbit}_{pair.orbit_state}"
    out = ctx.results.run_dir / "s1"
    nan = np.full(classes.shape, np.nan, dtype="float32")
    change_tif = write_geotiff(out / f"{tag}_change.tif", {
        "pre_vv_db": layers.pre_vv_db, "post_vv_db": layers.post_vv_db,
        "logratio_vv_db": layers.lr_vv,
        "logratio_vh_db": layers.lr_vh if layers.lr_vh is not None else nan,
        "pre_vh_db": layers.pre_vh_db if layers.pre_vh_db is not None else nan,
    }, transform, crs)
    classes_tif = write_geotiff(out / f"{tag}_classes.tif", {"class": classes}, transform, crs)

    pngs = []
    if primary:
        platforms = sorted({*pair.post.platforms, *(x for p in pair.pre for x in p.platforms)})
        subtitle = "\n".join([
            f"Track      {pair.relative_orbit} {pair.orbit_state}",
            f"Before     {', '.join(str(p.day) for p in pair.pre)}",
            f"After      {pair.post.day} ({pair.post_delay_days} d after event)",
            f"Platforms  {', '.join(platforms)}",
            f"Pixel      {resolution:g} m, UTM EPSG:{aoi.utm_epsg}",
            f"Thresholds {th['decrease_db']:.1f} / +{th['increase_db']:.1f} dB (VV or VH)",
            f"           ({th['decrease_method'].split(' ')[0]})",
            f"Water if after VV <= {params.water_post_vv_db:g} dB",
            "",
            "Area inside AOI (km²):",
            *[f"  {k[:30]:<30} {v:>9.3f}" for k, v in areas.items()],
            "",
            "Sentinel-1 only, before terrain",
            "masks (see confidence stage).",
        ])
        title = f"Sentinel-1 change — event {ctx.event_date} — {ctx.results.event_name}"
        pngs.append(render_s1(layers.pre_vv_db, layers.post_vv_db, layers.lr_vv, classes,
                              transform, title, subtitle, out / "s1_overview.png"))
        if ctx.focus_bbox:
            win = crop_window(transform, classes.shape, ctx.focus_bbox, aoi.utm_epsg)
            if win:
                pngs.append(render_s1(layers.pre_vv_db, layers.post_vv_db, layers.lr_vv,
                                      classes, transform, title + " (focus)", subtitle,
                                      out / "s1_focus.png", win))
    return {
        "pair": pair.describe(),
        "incidence_angle": pair.post.incidence_angle,
        "thresholds": th,
        "areas_km2": areas,
        "valid_share": round(valid_share, 3),
        "cached_download": cached,
        "download_mode": mode,
        "files": {"change": change_tif, "classes": classes_tif, "png": pngs},
    }


@register_stage("s1")
def run_s1(ctx: StageContext) -> StageResult:
    cfg, aoi, event = ctx.config.s1, ctx.aoi, ctx.event_date
    warnings: list[str] = []

    start = datetime.combine(event - timedelta(days=cfg.pre_window_days + 1), time(0), UTC)
    end = datetime.combine(event + timedelta(days=cfg.post_window_days + 1), time(0), UTC)
    ctx.progress("s1", "searching the Copernicus Data Space catalogue for Sentinel-1 images")
    scenes = search_s1_scenes(aoi.bounds, start, end, timeout_s=ctx.config.network.timeout_s,
                              retries=ctx.config.network.retries)
    try:
        pairs = rank_pairs(
            scenes, event, aoi.geometry, aoi.utm_epsg,
            pre_window_days=cfg.pre_window_days, post_window_days=cfg.post_window_days,
            max_pre_images=cfg.max_pre_images, min_coverage=cfg.min_coverage,
            include_event_day_as_post=cfg.include_event_day_as_post,
            same_pass_minutes=cfg.same_pass_minutes,
        )
    except NoPairError as exc:
        return StageResult("s1", StageStatus.FAILED, str(exc),
                           outputs={"scenes_found": len(scenes)})

    resolution = analysis_resolution(aoi, cfg.resolution_m, cfg.max_pixels)
    if resolution > cfg.resolution_m:
        warnings.append(f"large area: processed at {resolution:g} m instead of "
                        f"{cfg.resolution_m:g} m")

    tracks = []
    for i, pair in enumerate(select_tracks(pairs, cfg.max_tracks)):
        warnings.extend(f"track {pair.relative_orbit}: {n}" for n in pair.notes)
        ctx.progress("s1", f"track {pair.relative_orbit} {pair.orbit_state}: "
                           f"{len(pair.pre)} before image(s), after {pair.post.day}")
        try:
            tracks.append(_process_track(ctx, pair, resolution, primary=(i == 0),
                                         warnings=warnings))
        except Exception as exc:
            if i == 0:
                raise  # without the primary track there is no result
            log.warning("Secondary track failed", exc_info=True)
            warnings.append(f"second track {pair.relative_orbit} {pair.orbit_state} failed "
                            f"({type(exc).__name__}); continuing with one track")

    primary = tracks[0]
    areas = primary["areas_km2"]
    track_names = " + ".join(t["pair"]["track"] for t in tracks)
    return StageResult(
        "s1",
        StageStatus.PARTIAL if warnings else StageStatus.OK,
        f"{track_names}; primary after {primary['pair']['post']['start_utc'][:10]}: "
        f"water-like {areas[CLASS_NAMES[WATER]]:.2f} km², rough/debris-like "
        f"{areas[CLASS_NAMES[INCREASE]]:.2f} km² (before terrain masks)",
        outputs={
            "tracks": tracks,
            "unused_tracks": [p.describe()["track"] for p in pairs
                              if p.describe()["track"] not in track_names],
            "resolution_m": resolution,
            # convenience copies of the primary track (used by later stages and the copilot)
            "pair": primary["pair"],
            "areas_km2": areas,
            "files": primary["files"],
        },
        warnings=warnings,
    )
