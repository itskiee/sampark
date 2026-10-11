"""Stage "confidence": final flood/debris map with high/medium/low confidence.

Combines every available detection (Sentinel-1 primary track, Sentinel-1 opposite-pass
track, Sentinel-2 when cloud-free) inside the terrain-plausible zone, and writes:
  flood_map.tif        class + confidence + number of agreeing sources (radar grid)
  flood_areas.geojson  affected-area polygons (WGS84) with class and confidence (for M3+)
  flood_map.png        map over a Copernicus DEM hillshade (+ focus window)
"""

from __future__ import annotations

import logging

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.features import geometry_mask, shapes
from rasterio.warp import Resampling, reproject
from shapely.geometry import shape

from floodmap.flood_map import (
    CHANGE_CLASSES,
    CONF_NAMES,
    FINAL_CLASS_NAMES,
    Source,
    area_table,
    combine,
)
from floodmap.pipeline import StageContext, register_stage
from floodmap.results import StageResult, StageStatus
from floodmap.s1_change import write_geotiff
from floodmap.s1_pairing import to_utm
from floodmap.thresholds import NODATA
from floodmap.viz import crop_window, hillshade, render_flood_map

log = logging.getLogger("floodmap.stages.confidence")


def _read_band_by_name(path, name: str, shape, transform, crs, categorical: bool):
    with rasterio.open(path) as src:
        idx = list(src.descriptions).index(name) + 1
        data = src.read(idx)
        if src.shape == shape and src.transform == transform:
            return data
        dst = np.zeros(shape, dtype=data.dtype) if categorical else np.full(
            shape, np.nan, dtype="float32")
        reproject(data, dst, src_transform=src.transform, src_crs=src.crs,
                  dst_transform=transform, dst_crs=crs,
                  resampling=Resampling.nearest if categorical else Resampling.bilinear)
        return dst


def _polygons(final, conf, transform, epsg, inside) -> gpd.GeoDataFrame:
    flagged = np.isin(final, CHANGE_CLASSES) & inside
    code = (final.astype(np.int16) * 10 + conf.astype(np.int16))
    records = [{"geometry": shape(geom), "code": int(v)}
               for geom, v in shapes(code, mask=flagged, transform=transform)]
    if not records:
        return gpd.GeoDataFrame({"class": [], "confidence": [], "area_m2": []},
                                geometry=[], crs=f"EPSG:{epsg}").to_crs(4326)
    gdf = gpd.GeoDataFrame(records, crs=f"EPSG:{epsg}")
    gdf["class"] = [FINAL_CLASS_NAMES[c // 10] for c in gdf["code"]]
    gdf["confidence"] = [CONF_NAMES.get(c % 10, "none") for c in gdf["code"]]
    gdf["area_m2"] = gdf.geometry.area.round(0)
    gdf["status"] = "potentially affected"
    return gdf.drop(columns="code").to_crs(4326)


@register_stage("confidence", requires=("s1", "terrain"), uses=("s2",))
def run_confidence(ctx: StageContext) -> StageResult:
    cfg, aoi = ctx.config.confidence, ctx.aoi
    s1 = ctx.results.stages["s1"].outputs
    terr = ctx.results.stages["terrain"].outputs
    warnings: list[str] = []

    with rasterio.open(s1["files"]["change"]) as src:
        shape_, transform, crs = src.shape, src.transform, src.crs
    tpath = terr["files"]["terrain"]
    plausible = _read_band_by_name(tpath, "plausible", shape_, transform, crs, True) > 0.5
    perm = _read_band_by_name(tpath, "permanent_water", shape_, transform, crs, True) > 0.5

    sources = []
    for track in s1["tracks"]:
        tag = f"track{track['pair']['relative_orbit']}_{track['pair']['orbit_state']}"
        classes = _read_band_by_name(track["files"]["classes"], "class", shape_, transform,
                                     crs, True)
        geom_ok = _read_band_by_name(tpath, f"geom_ok_{tag}", shape_, transform, crs,
                                     True) > 0.5
        sources.append(Source(f"Sentinel-1 {track['pair']['track']}", classes,
                              (classes != NODATA) & geom_ok))
    s2_stage = ctx.results.stages.get("s2")
    if s2_stage is not None and s2_stage.usable and s2_stage.outputs.get("files", {}).get(
            "classes"):
        s2_classes = _read_band_by_name(s2_stage.outputs["files"]["classes"], "class", shape_,
                                        transform, crs, True)
        sources.append(Source("Sentinel-2 optical", s2_classes, s2_classes != NODATA))
    if len(sources) == 1:
        warnings.append("only one source available: confidence can be at most 'medium'")

    res = abs(transform.a)
    final, conf, agree = combine(sources, plausible, perm, cfg.neighbourhood_px,
                                 max(1, round(cfg.medium_min_patch_m2 / res**2)))
    inside = ~geometry_mask([to_utm(aoi.geometry, aoi.utm_epsg)], out_shape=shape_,
                            transform=transform)
    table = area_table(final, conf, res**2, inside)

    out = ctx.results.run_dir / "confidence"
    tif = write_geotiff(out / "flood_map.tif", {"class": final, "confidence": conf,
                                                "agreeing_sources": agree}, transform, crs)
    gdf = _polygons(final, conf, transform, aoi.utm_epsg, inside)
    geojson = out / "flood_areas.geojson"
    gdf.to_file(geojson, driver="GeoJSON")

    # Map over our own Copernicus DEM hillshade
    dem = _read_band_by_name(terr["files"]["hydrology"], "dem_m", shape_, transform, crs, False)
    shade = hillshade(dem, res)
    info = "\n".join([
        f"Sources ({len(sources)}):",
        *[f"  {s.name}" for s in sources],
        "",
        "Affected area inside AOI (km²):",
        *[f"  {k:<28} {v:>8.3f}" for k, v in table["by_class_km2"].items()
          if k in (FINAL_CLASS_NAMES[c] for c in CHANGE_CLASSES)],
        f"  {'total':<28} {table['affected_total_km2']:>8.3f}",
        "",
        "By confidence (km²):",
        *[f"  {k:<28} {v:>8.3f}" for k, v in table["affected_by_confidence_km2"].items()],
        "",
        f"Permanent water (before): {table['by_class_km2']['permanent water']:.3f} km²",
        "",
        "Plausible zone: HAND <= "
        f"{terr['hand_max_m']:g} m, slope <= {terr['slope_max_deg']:g}°",
        "high   = 2+ sources agree",
        "medium = 1 source, coherent patch",
        "low    = 1 source, small patch",
        "",
        "Status of every area:",
        "'potentially affected' — verify",
        "on the ground.",
    ])
    title = f"Flood & debris map with confidence — event {ctx.event_date} — " \
            f"{ctx.results.event_name}"
    pngs = [render_flood_map(shade, final, conf, transform, title, info, out / "flood_map.png")]
    if ctx.focus_bbox:
        win = crop_window(transform, shape_, ctx.focus_bbox, aoi.utm_epsg)
        if win:
            pngs.append(render_flood_map(shade, final, conf, transform, title + " (focus)",
                                         info, out / "flood_map_focus.png", win))

    by_conf = table["affected_by_confidence_km2"]
    return StageResult(
        "confidence", StageStatus.PARTIAL if warnings else StageStatus.OK,
        f"potentially affected {table['affected_total_km2']:.2f} km² (high {by_conf['high']:.2f},"
        f" medium {by_conf['medium']:.2f}, low {by_conf['low']:.2f}) from {len(sources)} sources",
        outputs={**table, "sources": [s.name for s in sources], "polygons": len(gdf),
                 "files": {"flood_map": tif, "polygons": geojson, "png": pngs}},
        warnings=warnings,
    )
