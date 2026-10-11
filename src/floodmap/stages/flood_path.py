"""Stage "flood_path" (official bonus): trace the flood path downstream from a point.

From any upstream point, follow the Copernicus DEM flow directions (D8, computed with pit
filling) down the valley. Output: the path as a line with distance markers, its length and
elevation drop, how much of it crosses detected flood/debris change, and (when the osm
stage ran) the pre-event OSM settlements along it: distance downstream, distance from the
path, height above the river at that point, and building count.
"""

from __future__ import annotations

import json

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer
from shapely.geometry import LineString, Point, mapping

from floodmap.flood_map import CHANGE_CLASSES
from floodmap.pipeline import StageContext, register_stage
from floodmap.results import StageResult, StageStatus
from floodmap.terrain import path_lengths_m, trace_flow_path


@register_stage("flood_path", requires=("terrain",), uses=("confidence", "osm"))
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
    settlements, places_xy = _settlements_along(ctx, xy, dist, elev, dem, transform)
    png = _render(ctx, terr, conf_stage, dem, transform, res, xy, length_km, drop, crossing,
                  settlements, places_xy, out / "flood_path.png")
    msg = f"traced {length_km} km downstream, elevation drop {drop} m"
    if crossing is not None:
        msg += f"; {crossing:.0%} of the path crosses detected flood/debris change"
    if settlements is not None:
        low = sum(s["low_lying"] for s in settlements)
        msg += (f"; {len(settlements)} settlements within {cfg.settlement_buffer_m:g} m "
                f"({low} low-lying)")
    return StageResult("flood_path", StageStatus.OK, msg, outputs={
        "start": {"lon": lon, "lat": lat},
        "snapped_start": {"lon": round(float(lons[0]), 6), "lat": round(float(lats[0]), 6)},
        "length_km": length_km,
        "elevation_start_m": round(elev_start, 1),
        "elevation_end_m": round(elev_end, 1),
        "share_crossing_detected_change": crossing,
        "km_markers": marks,
        "settlements_along_path": settlements,  # None if the osm stage did not run
        "settlement_buffer_m": cfg.settlement_buffer_m,
        "files": {"path": geojson, "png": [png]},
    })


def _settlements_along(ctx, xy, dist, elev, dem, dem_transform):
    """Pre-event OSM places within `settlement_buffer_m` of the path, in downstream order."""
    osm = ctx.results.stages.get("osm")
    if osm is None or not osm.usable:
        return None, None
    places = gpd.read_file(osm.outputs["files"]["gpkg"], layer="places")
    if places.empty:
        return [], None
    line = LineString(xy)
    buf = ctx.config.flood_path.settlement_buffer_m
    near = places[places.geometry.distance(line) <= buf].copy()
    rows = []
    hand_max = ctx.config.terrain.hand_max_m  # same "low ground" limit as the flood zone
    inv = ~dem_transform
    for _, p in near.iterrows():
        pt = Point(p.geometry.x, p.geometry.y)
        along = line.project(pt)
        i = min(int(np.searchsorted(dist, along)), len(dist) - 1)
        col, row = inv @ (pt.x, pt.y)
        r, c = int(row), int(col)
        z = dem[r, c] if 0 <= r < dem.shape[0] and 0 <= c < dem.shape[1] else np.nan
        above = float(z - elev[i]) if np.isfinite(z) and np.isfinite(elev[i]) else None
        count = pd.to_numeric(p.get("building_count"), errors="coerce")
        rows.append({"name": p["label"], "place": p["place"],
                     "km_downstream": round(along / 1000, 2),
                     "distance_from_path_m": round(float(line.distance(pt)), 0),
                     "height_above_river_m": None if above is None else round(above, 0),
                     "low_lying": above is not None and above <= hand_max,
                     "buildings": None if pd.isna(count) else int(count),
                     "x": pt.x, "y": pt.y})
    rows.sort(key=lambda d: d["km_downstream"])
    back = Transformer.from_crs(ctx.aoi.utm_epsg, 4326, always_xy=True)
    places_xy = [(d.pop("x"), d.pop("y"), d["name"]) for d in rows]
    for d, (x, y, _) in zip(rows, places_xy, strict=True):
        lon, lat = back.transform(x, y)
        d["lon"], d["lat"] = round(lon, 5), round(lat, 5)
    return rows, places_xy


def _render(ctx, terr, conf_stage, dem, dem_transform, res, xy, length_km, drop, crossing,
            settlements, places_xy, out_png):
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
        *(["Settlements along the path:", "  km   name (height above river)",
           f"  * = low-lying (<= {ctx.config.terrain.hand_max_m:g} m above the river)"]
          + [f"{'*' if s['low_lying'] else ' '} {s['km_downstream']:>5.1f} {s['name'][:20]:<20} "
             + (f"{s['height_above_river_m']:+.0f} m" if s["height_above_river_m"] is not None
                else "")
             for s in settlements[:30]]
          if settlements else ["No pre-event OSM settlements", "near the path (or OSM not run)."]),
    ])

    def overlay(ax):
        from matplotlib.lines import Line2D

        if not places_xy:
            return []
        ax.scatter([p[0] / 1000 for p in places_xy], [p[1] / 1000 for p in places_xy],
                   s=18, color="#ffd400", edgecolors="black", linewidths=0.6, zorder=6)
        for x, y, name in places_xy[:40]:
            if isinstance(name, str) and name.isascii():
                ax.annotate(name, (x / 1000, y / 1000), fontsize=6, xytext=(3, 3),
                            textcoords="offset points", zorder=7)
        return [Line2D([], [], marker="o", ls="", color="#ffd400", mec="black", ms=5,
                       label="settlement along path (OSM)")]

    return render_flood_map(shade, final, conf, transform,
                            f"Traced flood path (bonus) — {ctx.results.event_name}", info,
                            out_png, path_xy=xy, overlay=overlay)
