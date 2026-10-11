"""Stage "flood_path" (official bonus): trace the flood path downstream from a point.

From any upstream point, follow the Copernicus DEM flow directions (D8, computed with pit
filling) down the valley. Output: the path as a line with distance markers, its length and
elevation drop, and how much of it crosses detected flood/debris change. Settlements along
the path are added once pre-event OSM is available (milestone M3).
"""

from __future__ import annotations

import json

import numpy as np
import rasterio
from pyproj import Transformer
from shapely.geometry import LineString, mapping

from floodmap.flood_map import CHANGE_CLASSES
from floodmap.pipeline import StageContext, register_stage
from floodmap.results import StageResult, StageStatus
from floodmap.terrain import path_lengths_m, trace_flow_path


@register_stage("flood_path", requires=("terrain",))
def run_flood_path(ctx: StageContext) -> StageResult:
    if ctx.flood_path_start is None:
        return StageResult("flood_path", StageStatus.SKIPPED,
                           "No upstream start point given (event file `flood_path_start`, "
                           "CLI --flood-path-start LON LAT, or a click in the dashboard).")
    cfg, aoi = ctx.config.flood_path, ctx.aoi
    terr = ctx.results.stages["terrain"].outputs
    with rasterio.open(terr["files"]["hydrology"]) as src:
        fdir = src.read(1).astype("int32")
        acc = src.read(2)
        dem = src.read(4)
        transform, res = src.transform, src.res[0]

    lon, lat = ctx.flood_path_start
    to_utm = Transformer.from_crs(4326, aoi.utm_epsg, always_xy=True)
    x, y = to_utm.transform(lon, lat)
    col, row = (~transform) @ (x, y)
    row, col = int(row), int(col)
    if not (0 <= row < fdir.shape[0] and 0 <= col < fdir.shape[1]):
        return StageResult("flood_path", StageStatus.FAILED,
                           f"Start point ({lon:.4f}, {lat:.4f}) is outside the analysed area "
                           "(area + DEM buffer). Choose a point inside the area.")

    path = trace_flow_path(fdir, acc, (row, col), max(0, round(cfg.snap_radius_m / res)))
    if len(path) < 2:
        return StageResult("flood_path", StageStatus.FAILED,
                           "Could not trace a downstream path from this point (flat sink).")
    rc = np.asarray(path)
    xs, ys = transform @ (rc[:, 1] + 0.5, rc[:, 0] + 0.5)  # cell centres
    xy = np.column_stack([xs, ys])
    dist = path_lengths_m(path, res)
    elev = dem[rc[:, 0], rc[:, 1]]

    # Share of the path crossing detected change (if the confidence stage ran)
    crossing = None
    conf_stage = ctx.results.stages.get("confidence")
    if conf_stage is not None and conf_stage.usable:
        with rasterio.open(conf_stage.outputs["files"]["flood_map"]) as src:
            final = src.read(1)
            pc, pr = (~src.transform) @ (xs, ys)
            pr, pc = pr.astype(int), pc.astype(int)
            ok = (pr >= 0) & (pr < final.shape[0]) & (pc >= 0) & (pc < final.shape[1])
            crossing = round(float(np.isin(final[pr[ok], pc[ok]], CHANGE_CLASSES).mean()), 3)

    # Geometry (WGS84) + distance markers every km
    back = Transformer.from_crs(aoi.utm_epsg, 4326, always_xy=True)
    lons, lats = back.transform(xs, ys)
    line = LineString(np.column_stack([lons, lats]))
    marks = []
    for km in range(0, int(dist[-1] // 1000) + 1):
        i = int(np.searchsorted(dist, km * 1000))
        if i < len(path):
            marks.append({"km": km, "lon": round(float(lons[i]), 6),
                          "lat": round(float(lats[i]), 6),
                          "elevation_m": round(float(elev[i]), 1) if np.isfinite(elev[i])
                          else None})
    out = ctx.results.run_dir / "flood_path"
    out.mkdir(parents=True, exist_ok=True)
    geojson = out / "flood_path.geojson"
    geojson.write_text(json.dumps({"type": "FeatureCollection", "features": [{
        "type": "Feature", "geometry": mapping(line),
        "properties": {"length_km": round(float(dist[-1]) / 1000, 2),
                       "start_lon": lon, "start_lat": lat}}]}), encoding="utf-8")
    np.save(out / "path_xy_utm.npy", xy)  # used by the map renderer

    length_km = round(float(dist[-1]) / 1000, 2)
    finite = np.flatnonzero(np.isfinite(elev))  # the path may end on DEM no-data at the edge
    elev_start = float(elev[finite[0]]) if finite.size else float("nan")
    elev_end = float(elev[finite[-1]]) if finite.size else float("nan")
    drop = round(elev_start - elev_end, 1)
    png = _render(ctx, terr, conf_stage, dem, transform, res, xy, length_km, drop, crossing,
                  out / "flood_path.png")
    msg = f"traced {length_km} km downstream, elevation drop {drop} m"
    if crossing is not None:
        msg += f"; {crossing:.0%} of the path crosses detected flood/debris change"
    return StageResult("flood_path", StageStatus.OK, msg, outputs={
        "start": {"lon": lon, "lat": lat},
        "snapped_start": {"lon": round(float(lons[0]), 6), "lat": round(float(lats[0]), 6)},
        "length_km": length_km,
        "elevation_start_m": round(elev_start, 1),
        "elevation_end_m": round(elev_end, 1),
        "share_crossing_detected_change": crossing,
        "km_markers": marks,
        "settlements_along_path": [],  # filled in milestone M3 (pre-event OSM)
        "files": {"path": geojson, "png": [png]},
    })


def _render(ctx, terr, conf_stage, dem, dem_transform, res, xy, length_km, drop, crossing,
            out_png):
    """Path over the final flood map (if available) or over the DEM hillshade alone."""
    from floodmap.stages.confidence import _read_band_by_name
    from floodmap.viz import hillshade, render_flood_map

    if conf_stage is not None and conf_stage.usable:
        with rasterio.open(conf_stage.outputs["files"]["flood_map"]) as src:
            final, conf = src.read(1), src.read(2)
            transform, crs, shape = src.transform, src.crs, src.shape
        dem_grid = _read_band_by_name(terr["files"]["hydrology"], "dem_m", shape, transform,
                                      crs, False)
        shade = hillshade(dem_grid, abs(transform.a))
    else:
        final = np.zeros(dem.shape, dtype=np.uint8)
        conf = np.zeros(dem.shape, dtype=np.uint8)
        transform, shade = dem_transform, hillshade(dem, res)
    info = "\n".join([
        f"Start (lon, lat)  {ctx.flood_path_start[0]:.4f}, {ctx.flood_path_start[1]:.4f}",
        f"Length            {length_km} km",
        f"Elevation drop    {drop} m",
        "Crossing change   " + (f"{crossing:.0%}" if crossing is not None else "n/a"),
        "",
        "Method: Copernicus DEM, pits and",
        "flats filled, D8 flow direction;",
        "start snapped to the channel.",
        "",
        "Settlements along the path are",
        "added with pre-event OSM (M3).",
    ])
    return render_flood_map(shade, final, conf, transform,
                            f"Traced flood path (bonus) — {ctx.results.event_name}", info,
                            out_png, path_xy=xy)
