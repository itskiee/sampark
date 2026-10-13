"""Stage "landing_zones": helicopter landing-zone candidates for likely cut-off settlements.

For every settlement the cut-off analysis marks "likely cut off by road", the nearest
candidates (floodmap/landing_zones.py) within walking distance, with: straight-line distance,
height difference, and whether the straight walk crosses detected flood/debris (then a
candidate on the settlement's own side is preferred). Outputs:
landing_zones/landing_zones.geojson, landing_zones/landing_zones.png.
Every candidate must be verified on site (trees, wires, ground, approach): never "safe".
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import rasterio
import shapely
from matplotlib.lines import Line2D
from rasterio.features import rasterize
from shapely.geometry import LineString, Point

from floodmap.attribution import write_osm_license
from floodmap.damage import subdivide
from floodmap.landing_zones import candidates, nearest_for, suitable_mask
from floodmap.pipeline import StageContext, register_stage
from floodmap.results import StageResult, StageStatus
from floodmap.stages.osm import load_layers

VERIFY = "candidate only: verify trees, power lines, ground and approach on site"
BUILDING_BATCH = 20_000  # buildings rasterised per call (memory; result is identical)


def near_buildings_mask(geoms, buffer_m: float, shape, transform,
                        batch: int = BUILDING_BATCH) -> np.ndarray:
    """Pixels within `buffer_m` of any building, rasterised in batches: one call with every
    buffered building needed > 6 GB for Kathmandu's 454,718 buildings (2026-10-14). The
    union of the batches is exactly the same mask (test_landing_zones)."""
    mask = np.zeros(shape, bool)
    for i in range(0, len(geoms), batch):
        part = shapely.buffer(geoms[i:i + batch], buffer_m, quad_segs=16)  # = GeoSeries.buffer
        mask |= rasterize(part, out_shape=shape, transform=transform).astype(bool)
    return mask


@register_stage("landing_zones", requires=("cutoff", "terrain", "confidence", "osm"),
                uses=("priority",))
def run_landing_zones(ctx: StageContext) -> StageResult:
    from floodmap.stages.confidence import _read_band_by_name

    cfg, aoi = ctx.config.landing_zones, ctx.aoi
    cut = ctx.results.stages["cutoff"].outputs
    sett = gpd.read_file(cut["files"]["settlements"]).to_crs(aoi.utm_epsg)
    targets = sett[sett["motorable_any"] == "likely cut off"]
    if targets.empty:
        return StageResult("landing_zones", StageStatus.OK,
                           "no settlement is likely cut off by road: no landing zones needed",
                           outputs={"settlements_served": 0, "candidates": 0})

    conf_out = ctx.results.stages["confidence"].outputs
    with rasterio.open(conf_out["files"]["flood_map"]) as src:
        final = src.read(1)
        transform, crs, shape = src.transform, src.crs, src.shape
    terr = ctx.results.stages["terrain"].outputs["files"]
    slope = _read_band_by_name(terr["terrain"], "slope_deg", shape, transform, crs, False)
    dem = _read_band_by_name(terr["hydrology"], "dem_m", shape, transform, crs, False)
    buildings = load_layers(ctx.results.stages["osm"].outputs)["buildings"]
    near_b = near_buildings_mask(buildings.geometry.values, cfg.clear_of_buildings_m, shape,
                                 transform)
    del buildings
    res = abs(transform.a)
    ok = suitable_mask(slope, final, near_b, cfg.max_slope_deg)
    cands = candidates(ok, res, cfg.min_clear_square_m)
    cand_xy = np.array([transform @ (c + 0.5, r + 0.5) for r, c, _ in cands]) \
        if cands else np.zeros((0, 2))

    flood = gpd.read_file(conf_out["files"]["polygons"])
    flood = flood.to_crs(aoi.utm_epsg) if not flood.empty else flood
    # small pieces that exactly tile the flood area: a walk crosses the flood iff it touches
    # a piece (no slow merge of a huge flood into one shape; Storm Daniel test 2026-10-13)
    flood_tree = shapely.STRtree(subdivide(flood.geometry.values)) if not flood.empty else None
    sxy = np.column_stack([targets.geometry.x, targets.geometry.y])
    # look a bit wider than needed, then prefer candidates whose walk avoids flood/debris
    picks = nearest_for(sxy, cand_xy, cfg.max_distance_m, 4 * cfg.per_settlement)
    rows, served = [], 0
    for (_, s), options in zip(targets.iterrows(), picks, strict=True):
        scored = []
        for ci, dist in options:
            r, c, square = cands[ci]
            walk = LineString([(s.geometry.x, s.geometry.y), tuple(cand_xy[ci])])
            crosses = bool(flood_tree is not None
                           and len(flood_tree.query(walk, predicate="intersects")))
            z_lz = float(dem[r, c]) if np.isfinite(dem[r, c]) else None
            sr, sc = ~transform @ (s.geometry.x, s.geometry.y)
            sr, sc = int(sc), int(sr)
            z_s = float(dem[sr, sc]) if 0 <= sr < shape[0] and 0 <= sc < shape[1] and \
                np.isfinite(dem[sr, sc]) else None
            scored.append((crosses, dist, ci, square, z_lz, z_s))
        scored.sort(key=lambda t: (t[0], t[1]))
        if scored:
            served += 1
        for rank, (crosses, dist, ci, square, z_lz, z_s) in \
                enumerate(scored[: cfg.per_settlement], start=1):
            rows.append({"settlement_id": s["settlement_id"], "settlement": s["name"],
                         "option": rank, "distance_m": round(dist, 0),
                         "height_difference_m": round(z_lz - z_s, 0)
                         if z_lz is not None and z_s is not None else None,
                         "walk_crosses_detected_flood": crosses,
                         "clear_square_m": square,
                         "slope_deg": round(float(slope[cands[ci][0], cands[ci][1]]), 1),
                         "status": VERIFY, "geometry": Point(cand_xy[ci])})

    out = ctx.results.run_dir / "landing_zones"
    out.mkdir(parents=True, exist_ok=True)
    write_osm_license(out)  # proximity to OSM buildings is part of the result
    geo = out / "landing_zones.geojson"
    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs=aoi.utm_epsg) if rows else None
    if gdf is not None:
        gdf.assign(walk_crosses_detected_flood=gdf["walk_crosses_detected_flood"].astype(int)
                   ).to_crs(4326).to_file(geo, driver="GeoJSON")
    else:
        geo.write_text('{"type": "FeatureCollection", "features": []}', encoding="utf-8")
    lonlat = gdf.to_crs(4326).geometry if gdf is not None else []
    listed = [{k: v for k, v in r.items() if k != "geometry"} | {"lon": round(p.x, 5),
                                                                "lat": round(p.y, 5)}
              for r, p in zip(rows, lonlat, strict=True)]
    png = _render(ctx, conf_out, final, transform, targets, gdf, served, len(targets))
    msg = (f"candidates for {served} of {len(targets)} likely cut-off settlements "
           f"(within {cfg.max_distance_m / 1000:g} km); {VERIFY}")
    return StageResult("landing_zones", StageStatus.OK, msg, outputs={
        "settlements_served": served, "likely_cut_off_settlements": len(targets),
        "candidate_sites_in_area": len(cands),
        "criteria": {"max_slope_deg": cfg.max_slope_deg,
                     "min_clear_square_m": cfg.min_clear_square_m,
                     "clear_of_buildings_m": cfg.clear_of_buildings_m,
                     "max_distance_m": cfg.max_distance_m,
                     "excluded": "detected flood/debris, permanent water, no radar view"},
        "options": listed[: 3 * ctx.config.cutoff.max_listed],
        "status_wording": VERIFY,
        "files": {"geojson": geo, "png": [png]}})


def _render(ctx, conf_out, final, transform, targets, gdf, served, total):
    from floodmap.stages.confidence import _read_band_by_name
    from floodmap.viz import crop_window, hillshade, render_flood_map

    terr = ctx.results.stages["terrain"].outputs["files"]
    with rasterio.open(conf_out["files"]["flood_map"]) as src:
        conf, crs, shape = src.read(2), src.crs, src.shape
    dem = _read_band_by_name(terr["hydrology"], "dem_m", shape, transform, crs, False)
    shade = hillshade(dem, abs(transform.a))

    def overlay(ax):
        ax.scatter(targets.geometry.x / 1000, targets.geometry.y / 1000, s=22,
                   color="#d7191c", edgecolors="black", linewidths=0.4, zorder=5)
        if gdf is not None:
            pairs = targets.set_index("settlement_id").geometry
            for _, r in gdf.iterrows():
                s = pairs.get(r["settlement_id"])
                if s is not None:
                    ax.plot([s.x / 1000, r.geometry.x / 1000], [s.y / 1000, r.geometry.y / 1000],
                            color="#1a9641", lw=0.7, ls="--", zorder=5)
            ax.scatter(gdf.geometry.x / 1000, gdf.geometry.y / 1000, marker="H", s=60,
                       color="#1a9641", edgecolors="black", linewidths=0.6, zorder=6)
        return [Line2D([], [], marker="o", ls="", color="#d7191c", mec="black", ms=6,
                       label="settlement likely cut off by road"),
                Line2D([], [], marker="H", ls="", color="#1a9641", mec="black", ms=8,
                       label="landing-zone candidate (verify on site)")]

    c = ctx.config.landing_zones
    info = "\n".join([
        f"Candidates for {served} of {total}",
        "settlements likely cut off by road.",
        "",
        "Criteria (Copernicus DEM, our flood",
        "map, pre-event OSM buildings):",
        f"  slope <= {c.max_slope_deg:g} degrees",
        f"  clear square >= {c.min_clear_square_m:g} m",
        f"  >= {c.clear_of_buildings_m:g} m from buildings",
        "  no detected flood/debris or water",
        f"  within {c.max_distance_m / 1000:g} km of the settlement",
        "",
        "CANDIDATES ONLY: trees, power lines,",
        "ground and approach cannot be seen",
        "in 20-30 m data. Verify on site.",
    ])
    title = f"Helicopter landing-zone candidates — event {ctx.event_date} — " \
            f"{ctx.results.event_name}"
    out = ctx.results.run_dir / "landing_zones"
    win = crop_window(transform, shape, ctx.focus_bbox, ctx.aoi.utm_epsg) \
        if ctx.focus_bbox else None
    return render_flood_map(shade, final, conf, transform, title, info,
                            out / "landing_zones.png", win, overlay=overlay)
