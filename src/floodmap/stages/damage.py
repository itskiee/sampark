"""Stage "damage": which pre-event OSM assets are potentially affected.

Overlays the affected-area polygons of the confidence stage with pre-event OSM buildings,
roads, bridges and health facilities (see floodmap/damage.py for the rules), and
summarises per settlement (building counts = the brief's population proxy).
Outputs (WGS84 GeoJSON, for the dashboard, exports and the cut-off analysis):
  damage/buildings_affected.geojson, roads_affected.geojson, bridges_affected.geojson,
  facilities_affected.geojson, settlements_affected.geojson, damage_map.png (+ focus)
"""

from __future__ import annotations

import logging

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from matplotlib.lines import Line2D

from floodmap.attribution import write_osm_license
from floodmap.damage import (
    CLASS_PRIORITY,
    CONF_RANK,
    affected_buildings,
    affected_points,
    affected_roads,
    assess_bridges,
    count_by,
)
from floodmap.osm import display_name, nearest_place
from floodmap.pipeline import StageContext, register_stage
from floodmap.results import StageResult, StageStatus
from floodmap.stages.osm import load_layers

log = logging.getLogger("floodmap.stages.damage")
CONF_ORDER = ["high", "medium", "low"]


def _lonlat(gdf: gpd.GeoDataFrame) -> list[tuple[float, float]]:
    pts = gdf.geometry.representative_point().to_crs(4326)
    return [(round(p.x, 5), round(p.y, 5)) for p in pts]


def _save(gdf: gpd.GeoDataFrame, path) -> None:
    if gdf.empty:  # GDAL cannot always infer a schema for an empty layer
        path.write_text('{"type": "FeatureCollection", "features": []}', encoding="utf-8")
        return
    out = gdf.copy()
    for col in out.columns:  # GeoJSON cannot store numpy bools / mixed objects reliably
        if col != "geometry" and out[col].dtype == bool:
            out[col] = out[col].astype(int)
    out.to_crs(4326).to_file(path, driver="GeoJSON")


def _sort_by_confidence(frame: pd.DataFrame, *extra: str) -> pd.DataFrame:
    if frame.empty:
        return frame
    keys = frame.assign(_rank=frame["confidence"].map(CONF_RANK).fillna(0))
    return keys.sort_values(["_rank", *extra], ascending=False).drop(columns="_rank")


def _corridor(ctx: StageContext):
    """P19: the traced flood path buffered by `corridor_buffer_m` (UTM), or None."""
    fp = ctx.results.stages.get("flood_path")
    if fp is None or not fp.usable:
        return None
    line = gpd.read_file(fp.outputs["files"]["path"]).to_crs(ctx.aoi.utm_epsg)
    return line.geometry.iloc[0].buffer(ctx.config.flood_path.corridor_buffer_m)


def _tag_corridor(gdf: gpd.GeoDataFrame, corridor) -> gpd.GeoDataFrame:
    if corridor is None:
        return gdf
    flags = gdf.geometry.intersects(corridor).astype(bool) if not gdf.empty else []
    return gdf.assign(on_flood_corridor=flags)


@register_stage("damage", requires=("osm", "confidence", "terrain"), uses=("flood_path",))
def run_damage(ctx: StageContext) -> StageResult:
    cfg, aoi = ctx.config.damage, ctx.aoi
    radius = ctx.config.osm.settlement_radius_m
    layers = load_layers(ctx.results.stages["osm"].outputs)
    conf_out = ctx.results.stages["confidence"].outputs
    flood = gpd.read_file(conf_out["files"]["polygons"])
    flood = flood.to_crs(aoi.utm_epsg) if not flood.empty else flood.set_crs(
        4326, allow_override=True).to_crs(aoi.utm_epsg)
    buildings, roads, places = layers["buildings"], layers["roads"], layers["places"]
    health = layers["health"]
    warnings: list[str] = list(ctx.results.stages["osm"].warnings)

    # Buildings ----------------------------------------------------------------------
    hit_b = affected_buildings(buildings, flood, cfg.building_buffer_m)
    # Roads and bridges -----------------------------------------------------------------
    hit_r = affected_roads(roads, flood, cfg.road_buffer_m)
    bridges = roads[roads["is_bridge"]]
    hit_br = assess_bridges(bridges, flood, cfg.bridge_approach_m, cfg.bridge_reach_m)
    hit_br["label"] = [display_name(r, f"unnamed {r['highway']} bridge")
                       for _, r in hit_br.iterrows()]
    hit_br["near_place"] = [None if i is None else places.loc[i, "label"]
                            for i in nearest_place(hit_br, places, 3 * radius)]
    # Facilities --------------------------------------------------------------------------
    hit_h = affected_points(health, flood, cfg.facility_buffer_m)

    # P19: on the traced flood corridor or elsewhere -------------------------------------
    corridor = _corridor(ctx)
    hit_b, hit_r, hit_br, hit_h = (_tag_corridor(g, corridor)
                                   for g in (hit_b, hit_r, hit_br, hit_h))

    # Settlements (P18 building groups) ---------------------------------------------------
    sett = layers["settlements"].copy()
    sett["label"] = sett["name"]
    sid = hit_b["settlement_id"] if "settlement_id" in hit_b else pd.Series(dtype=object)
    is_high = hit_b["confidence"] == "high" if not hit_b.empty else pd.Series(dtype=bool)
    counts = sid[sid != ""].value_counts()
    high = sid[(sid != "") & is_high].value_counts()
    sett["affected_buildings"] = [int(counts.get(i, 0)) for i in sett["settlement_id"]]
    sett["affected_high"] = [int(high.get(i, 0)) for i in sett["settlement_id"]]
    sett = _tag_corridor(sett, corridor)
    total = pd.to_numeric(sett["building_count"], errors="coerce")
    sett["affected_share"] = np.where(total > 0, (sett["affected_buildings"] / total).round(3),
                                      np.nan)
    sett_hit = sett[sett["affected_buildings"] > 0].sort_values(
        ["affected_buildings", "affected_high"], ascending=False)

    out = ctx.results.run_dir / "damage"
    out.mkdir(parents=True, exist_ok=True)
    write_osm_license(out)  # affected-asset files are modified OSM data
    files = {
        "buildings": out / "buildings_affected.geojson",
        "roads": out / "roads_affected.geojson",
        "bridges": out / "bridges_affected.geojson",
        "facilities": out / "facilities_affected.geojson",
        "settlements": out / "settlements_affected.geojson",
    }
    _save(hit_b, files["buildings"])
    _save(hit_r, files["roads"])
    _save(hit_br, files["bridges"])
    _save(hit_h, files["facilities"])
    _save(sett_hit, files["settlements"])

    n = cfg.max_listed
    named = hit_r.assign(road=[display_name(r, r["ref"] or f"unnamed {r['highway']}")
                               for _, r in hit_r.iterrows()])
    road_list = (named.groupby(["road", "highway"], dropna=False)
                 .agg(affected_m=("affected_m", "sum"),
                      motorable=("motorable", "max"),
                      rank=("confidence", lambda s: s.map(CONF_RANK).max()))
                 .reset_index().sort_values("affected_m", ascending=False).head(n)
                 ) if not hit_r.empty else pd.DataFrame()
    bridge_rows = _sort_by_confidence(hit_br.assign(m=hit_br["motorable"].astype(int)), "m")
    outputs = {
        "buildings": {
            "potentially_affected": len(hit_b),
            "of_total_in_area": len(buildings),
            "by_confidence": count_by(hit_b, "confidence", CONF_ORDER),
            "by_class": count_by(hit_b, "flood_class", CLASS_PRIORITY),
        },
        "roads": {
            "affected_km_motorable": round(float(hit_r.loc[hit_r["motorable"], "affected_m"]
                                                 .sum() / 1000), 2) if not hit_r.empty else 0.0,
            "affected_km_all_ways": round(float(hit_r["affected_m"].sum() / 1000), 2)
            if not hit_r.empty else 0.0,
            "affected_ways": len(hit_r),
            "most_affected": [{"road": r["road"], "highway": r["highway"],
                               "affected_m": round(float(r["affected_m"]), 0),
                               "motorable": bool(r["motorable"]),
                               "confidence": {v: k for k, v in CONF_RANK.items()}[
                                   int(r["rank"])]}
                              for _, r in road_list.iterrows()],
        },
        "bridges": {
            "potentially_affected": len(hit_br),
            "of_total": len(bridges),
            "motorable_affected": int(hit_br["motorable"].sum()) if not hit_br.empty else 0,
            "by_evidence": count_by(hit_br, "evidence", ["deck/approach", "river reach"]),
            "list": [{"name": r["label"], "highway": r["highway"],
                      "motorable": bool(r["motorable"]), "evidence": r["evidence"],
                      "confidence": r["confidence"], "near_settlement": r["near_place"],
                      "length_m": round(float(r["length_m"]), 0), "lon": ll[0], "lat": ll[1]}
                     for (_, r), ll in zip(bridge_rows.head(n).iterrows(),
                                           _lonlat(bridge_rows.head(n)), strict=True)],
        },
        "health_facilities": [
            {"name": r["label"], "category": r["category"], "confidence": r["confidence"],
             "flood_class": r["flood_class"], "lon": ll[0], "lat": ll[1]}
            for (_, r), ll in zip(hit_h.iterrows(), _lonlat(hit_h), strict=True)],
        "settlements": {
            "with_affected_buildings": len(sett_hit),
            "affected_buildings_outside_settlements": int((sid == "").sum()),
            "most_affected": [
                {"settlement_id": r["settlement_id"], "name": r["label"], "place": r["place"],
                 **({"on_flood_corridor": bool(r["on_flood_corridor"])}
                    if corridor is not None else {}),
                 "buildings": None if pd.isna(r["building_count"]) else int(r["building_count"]),
                 "potentially_affected_buildings": int(r["affected_buildings"]),
                 "of_which_high_confidence": int(r["affected_high"]),
                 "share": None if pd.isna(r["affected_share"]) else float(r["affected_share"]),
                 "lon": ll[0], "lat": ll[1]}
                for (_, r), ll in zip(sett_hit.head(n).iterrows(), _lonlat(sett_hit.head(n)),
                                      strict=True)],
        },
        "buffers_m": {"building": cfg.building_buffer_m, "road": cfg.road_buffer_m,
                      "facility": cfg.facility_buffer_m,
                      "bridge_approach": cfg.bridge_approach_m,
                      "bridge_reach": cfg.bridge_reach_m},
        "status_wording": "potentially affected (estimate; verify on the ground)",
    }
    if corridor is not None:  # P19 summary
        def on(frame):
            return int(frame["on_flood_corridor"].sum()) if not frame.empty else 0
        outputs["flood_corridor"] = {
            "buffer_m": ctx.config.flood_path.corridor_buffer_m,
            "buildings_on_corridor": on(hit_b),
            "buildings_elsewhere": len(hit_b) - on(hit_b),
            "bridges_on_corridor": on(hit_br),
            "road_km_on_corridor": round(float(hit_r.loc[hit_r["on_flood_corridor"],
                                                         "affected_m"].sum() / 1000), 2)
            if not hit_r.empty else 0.0,
            "meaning": "on corridor = within the buffer of the traced flood path (most likely "
                       "this event); elsewhere = other rivers/fields, check separately",
        }
    pngs = _render(ctx, conf_out, buildings, roads, hit_b, hit_r, hit_br, health, sett_hit,
                   outputs, out)
    outputs["files"] = {**files, "png": pngs}

    b = outputs["buildings"]
    msg = (f"{b['potentially_affected']:,} of {b['of_total_in_area']:,} buildings potentially "
           f"affected (high {b['by_confidence']['high']:,}); "
           f"{outputs['roads']['affected_km_motorable']:.1f} km motorable road; "
           f"{len(hit_br)} bridges; {len(hit_h)} health facilities")
    return StageResult("damage", StageStatus.PARTIAL if warnings else StageStatus.OK, msg,
                       outputs=outputs, warnings=warnings)


def _render(ctx, conf_out, buildings, roads, hit_b, hit_r, hit_br, health, sett_hit, outputs,
            out):
    from floodmap.stages.confidence import _read_band_by_name
    from floodmap.viz import crop_window, draw_lines, hillshade, render_flood_map

    terr = ctx.results.stages["terrain"].outputs
    with rasterio.open(conf_out["files"]["flood_map"]) as src:
        final, conf = src.read(1), src.read(2)
        transform, crs, shape = src.transform, src.crs, src.shape
    dem = _read_band_by_name(terr["files"]["hydrology"], "dem_m", shape, transform, crs, False)
    shade = hillshade(dem, abs(transform.a))

    def overlay(ax):
        draw_lines(ax, roads.geometry[~roads["motorable"]], colors="#8c8c8c", linewidths=0.3)
        draw_lines(ax, roads.geometry[roads["motorable"]], colors="#404040", linewidths=0.7)
        draw_lines(ax, hit_r.geometry, colors="#d7191c", linewidths=1.6)
        if not buildings.empty:
            c = buildings.geometry.centroid
            ax.scatter(c.x / 1000, c.y / 1000, s=0.3, color="#525252", linewidths=0)
        if not hit_b.empty:
            c = hit_b.geometry.centroid
            ax.scatter(c.x / 1000, c.y / 1000, s=3, color="#d7191c", linewidths=0)
        for evidence, color in (("deck/approach", "#d7191c"), ("river reach", "#fdae61")):
            sel = hit_br[hit_br["evidence"] == evidence]
            if not sel.empty:
                p = sel.geometry.representative_point()
                ax.scatter(p.x / 1000, p.y / 1000, marker="^", s=55, color=color,
                           edgecolors="black", linewidths=0.6, zorder=5)
        hosp = health[health["category"] == "hospital"]
        if not hosp.empty:
            ax.scatter(hosp.geometry.x / 1000, hosp.geometry.y / 1000, marker="P", s=70,
                       color="white", edgecolors="#006d2c", linewidths=1.2, zorder=6)
        for _, r in sett_hit.head(12).iterrows():
            if isinstance(r["label"], str) and r["label"].isascii():
                ax.annotate(f"{r['label']} ({int(r['affected_buildings'])})",
                            (r.geometry.x / 1000, r.geometry.y / 1000), fontsize=7,
                            xytext=(4, 4), textcoords="offset points", zorder=7,
                            bbox={"facecolor": "white", "alpha": 0.7, "lw": 0, "pad": 1})
        return [Line2D([], [], color="#404040", lw=1, label="road (motorable)"),
                Line2D([], [], color="#d7191c", lw=2, label="road in flood/debris"),
                Line2D([], [], marker="o", ls="", color="#d7191c", ms=3,
                       label="building potentially affected"),
                Line2D([], [], marker="^", ls="", color="#d7191c", mec="black", ms=7,
                       label="bridge: deck/approach"),
                Line2D([], [], marker="^", ls="", color="#fdae61", mec="black", ms=7,
                       label="bridge: changed reach nearby"),
                Line2D([], [], marker="P", ls="", color="white", mec="#006d2c", ms=8,
                       label="hospital")]

    b, r, br = outputs["buildings"], outputs["roads"], outputs["bridges"]
    info = "\n".join([
        f"OSM snapshot: {ctx.results.stages['osm'].outputs['snapshot_utc'][:16]}Z",
        "(pre-event, ohsome API)",
        "",
        "Potentially affected:",
        f"  buildings      {b['potentially_affected']:>7,} / {b['of_total_in_area']:,}",
        *[f"    {k:<12} {v:>7,}" for k, v in b["by_confidence"].items()],
        f"  motorable road {r['affected_km_motorable']:>7.1f} km",
        f"  all ways       {r['affected_km_all_ways']:>7.1f} km",
        f"  bridges        {br['potentially_affected']:>7} / {br['of_total']}",
        f"  health fac.    {len(outputs['health_facilities']):>7}",
        "",
        "Most affected settlements",
        "(buildings potentially affected):",
        *[f"  {s['name'][:22]:<22} {s['potentially_affected_buildings']:>5}"
          for s in outputs["settlements"]["most_affected"][:10]],
        "",
        "Status: 'potentially affected'.",
        "Estimate from satellite change",
        "detection; verify on the ground.",
    ])
    title = f"Potentially affected assets — event {ctx.event_date} — {ctx.results.event_name}"
    pngs = [render_flood_map(shade, final, conf, transform, title, info,
                             out / "damage_map.png", overlay=overlay)]
    if ctx.focus_bbox:
        win = crop_window(transform, shape, ctx.focus_bbox, ctx.aoi.utm_epsg)
        if win:
            pngs.append(render_flood_map(shade, final, conf, transform, title + " (focus)",
                                         info, out / "damage_map_focus.png", win,
                                         overlay=overlay))
    return pngs
