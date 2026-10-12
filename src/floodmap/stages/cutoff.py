"""Stage "cutoff": which settlements lost their road connection to the nearest town or
hospital (brief: "Who is cut off?"), where the road is likely impassable and how far away.

For every settlement (P18 building groups + named OSM places) and both networks
(motorable roads, all ways incl. footpaths) we compare before vs after the event:
  headline set   nearest town, hospital, or road leaving the area (P9 "outside")
  hospital set   nearest hospital only (to say "likely cut off from <hospital>")
Output: cutoff/settlements_cutoff.geojson, cutoff/road_breaks.geojson, cutoff_map.png and
one plain sentence per cut-off settlement in the brief's form:
  "Village X: likely cut off by road from <hospital>; road likely impassable at
   <segment/bridge>, d km away (<confidence>). Needs field verification."
"""

from __future__ import annotations

import json
import logging

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from matplotlib.lines import Line2D
from shapely.geometry import box

from floodmap.attribution import write_osm_license
from floodmap.cutoff import (
    build_graph,
    classify,
    edge_confidence,
    edge_lookup,
    first_break,
    reach,
    snap,
)
from floodmap.damage import CONF_RANK, RANK_CONF, assess_bridges
from floodmap.osm import display_name
from floodmap.pipeline import StageContext, register_stage
from floodmap.results import StageResult, StageStatus
from floodmap.s1_pairing import to_utm
from floodmap.stages.osm import load_layers
from floodmap.terrain import buffered_bounds

log = logging.getLogger("floodmap.stages.cutoff")
VARIANTS = ("motorable", "all_ways")
STATUSES = ("likely cut off", "possibly cut off", "detour", "connected",
            "no access before the event")
OUTSIDE_NAME = "a road leaving the analysed area"


def _road_label(row) -> str:
    base = display_name(row, row.get("ref") or f"unnamed {row['highway']}")
    return f"bridge '{base}'" if row.get("is_bridge") else base


@register_stage("cutoff", requires=("osm", "confidence", "terrain"))
def run_cutoff(ctx: StageContext) -> StageResult:
    cfg, dcfg, aoi = ctx.config.cutoff, ctx.config.damage, ctx.aoi
    layers = load_layers(ctx.results.stages["osm"].outputs)
    roads, settle, health, places = (layers[k] for k in ("roads", "settlements", "health",
                                                          "places"))
    if roads.empty:
        return StageResult("cutoff", StageStatus.SKIPPED,
                           "No roads mapped in pre-event OSM here: cut-off cannot be assessed.")
    if settle.empty:
        return StageResult("cutoff", StageStatus.SKIPPED,
                           "No settlements (buildings or named places) in pre-event OSM here.")
    flood = gpd.read_file(ctx.results.stages["confidence"].outputs["files"]["polygons"])
    flood = flood.to_crs(aoi.utm_epsg) if not flood.empty else flood.set_crs(
        4326, allow_override=True).to_crs(aoi.utm_epsg)
    warnings: list[str] = []

    net_area = to_utm(box(*buffered_bounds(aoi, ctx.config.osm.network_buffer_m)),
                      aoi.utm_epsg).buffer(-cfg.boundary_margin_m)
    bridges = assess_bridges(roads[roads["is_bridge"]], flood, dcfg.bridge_approach_m,
                             dcfg.bridge_reach_m)
    bridge_rank = {} if bridges.empty else {
        int(i): int(CONF_RANK.get(c, 0))
        for i, c in zip(bridges.index, bridges["confidence"], strict=True)}
    threshold = CONF_RANK[cfg.break_min_confidence]

    hospitals = health[health["category"] == "hospital"]
    towns = places[places["place"].isin(cfg.town_places)]
    if hospitals.empty:
        warnings.append("no hospital in pre-event OSM within the area + network buffer: "
                        "hospital access cannot be assessed")
    rows = {sid: {} for sid in settle["settlement_id"]}
    breaks: dict[str, list] = {v: [] for v in VARIANTS}
    graphs = {}
    for variant in VARIANTS:
        sub = roads[roads["motorable"]] if variant == "motorable" else roads
        g = build_graph(sub, net_area, cfg.densify_m)
        graphs[variant] = g
        if len(g.xy) == 0:
            warnings.append(f"no {variant.replace('_', ' ')} roads mapped")
            continue
        evidence = edge_confidence(g, flood, dcfg.road_buffer_m, bridge_rank)
        main = evidence >= threshold  # "likely impassable": medium/high by default
        worst = evidence >= 1  # incl. low-confidence: "possibly cut off"
        keep_all = np.ones(len(g.u), dtype=bool)
        s_node = snap(g, settle.geometry, cfg.snap_max_m)
        h_node = snap(g, hospitals.geometry, cfg.snap_max_m)
        t_node = snap(g, towns.geometry, cfg.snap_max_m)
        names = {g.outside: OUTSIDE_NAME}
        for nodes, frame in ((t_node, towns), (h_node, hospitals)):  # hospital name wins
            for n, label in zip(nodes, frame["label"], strict=True):
                if n >= 0:
                    names[int(n)] = label
        hosp_set = [int(n) for n in h_node if n >= 0]
        any_set = sorted({*hosp_set, *(int(n) for n in t_node if n >= 0), g.outside})
        lookup = edge_lookup(g)
        res = {}
        for key, dest in (("any", any_set), ("hospital", hosp_set)):
            res[key] = (reach(g, keep_all, dest), reach(g, ~main, dest), reach(g, ~worst, dest))
        for sid, n in zip(settle["settlement_id"], s_node, strict=True):
            rec = rows[sid].setdefault(variant, {})
            for key, (b0, b1, bw) in res.items():
                d0 = b0.dist[n] if n >= 0 else np.inf
                d1 = b1.dist[n] if n >= 0 else np.inf
                dw = bw.dist[n] if n >= 0 else np.inf
                status = classify(d0, d1, dw, cfg.detour_factor, cfg.detour_min_extra_km * 1000)
                rec[key] = {
                    "status": status,
                    "before_km": round(d0 / 1000, 2) if np.isfinite(d0) else None,
                    "after_km": round(d1 / 1000, 2) if np.isfinite(d1) else None,
                    "destination_before": names.get(int(b0.source[n])) if n >= 0 and
                    b0.source[n] >= 0 else None,
                    "destination_after": names.get(int(b1.source[n])) if n >= 0 and
                    b1.source[n] >= 0 else None,
                }
                if status == "likely cut off" and key == "any":
                    hit = first_break(g, b0, int(n), main, lookup)
                    if hit is not None:
                        e, dist_m = hit
                        way = roads.loc[int(g.way[e])]
                        mid = (g.xy[g.u[e]] + g.xy[g.v[e]]) / 2
                        rec["break"] = {"road": _road_label(way), "osm_id": way["osm_id"],
                                        "is_bridge": bool(way["is_bridge"]),
                                        "distance_km": round(dist_m / 1000, 2),
                                        "confidence": RANK_CONF[int(evidence[e])],
                                        "x": float(mid[0]), "y": float(mid[1])}
                        breaks[variant].append((sid, int(g.way[e]), mid))

    # Table per settlement -----------------------------------------------------------------
    table = settle.copy()
    for variant in VARIANTS:
        for key in ("any", "hospital"):
            table[f"{variant}_{key}"] = [rows[s].get(variant, {}).get(key, {}).get(
                "status", "no access before the event") for s in table["settlement_id"]]
    table["sentence"] = [_sentence(r["name"], rows[r["settlement_id"]])
                         for _, r in table.iterrows()]
    back = gpd.GeoSeries(gpd.points_from_xy(
        [rows[s].get("motorable", {}).get("break", {}).get("x", np.nan) for s in
         table["settlement_id"]],
        [rows[s].get("motorable", {}).get("break", {}).get("y", np.nan) for s in
         table["settlement_id"]]), crs=aoi.utm_epsg).to_crs(4326)
    lonlat = table.geometry.to_crs(4326)
    for (_, r), ll, bl in zip(table.iterrows(), lonlat, back, strict=True):
        rec = rows[r["settlement_id"]]
        rec["lon"], rec["lat"] = round(ll.x, 5), round(ll.y, 5)
        brk = rec.get("motorable", {}).get("break")
        if brk:
            brk["lon"], brk["lat"] = round(bl.x, 5), round(bl.y, 5)
            brk.pop("x"), brk.pop("y")
        for v in VARIANTS:
            vb = rec.get(v, {}).get("break")
            if vb and "x" in vb:
                vb.pop("x"), vb.pop("y")

    # Key breaks: which broken road/bridge cut off the most settlements (by road) ----------
    key_breaks = _key_breaks(breaks["motorable"], roads, table, aoi.utm_epsg)

    out = ctx.results.run_dir / "cutoff"
    out.mkdir(parents=True, exist_ok=True)
    write_osm_license(out)
    files = {"settlements": out / "settlements_cutoff.geojson",
             "breaks": out / "road_breaks.geojson"}
    table.drop(columns=[c for c in table.columns if c == "label"]).to_crs(4326).to_file(
        files["settlements"], driver="GeoJSON")
    _save_breaks(key_breaks, files["breaks"])

    summary = {}
    for variant in VARIANTS:
        counts = table[f"{variant}_any"].value_counts()
        cut = table[table[f"{variant}_any"] == "likely cut off"]
        summary[variant] = {
            **{s: int(counts.get(s, 0)) for s in STATUSES},
            "buildings_in_cut_off_settlements": int(cut["building_count"].fillna(0).sum()),
            "lost_hospital_access": int((table[f"{variant}_hospital"] == "likely cut off").sum()),
        }
    n = cfg.max_listed
    cut = table[table["motorable_any"] == "likely cut off"].sort_values("building_count",
                                                                 ascending=False)
    outputs = {
        "settlements_assessed": len(table),
        "by_network": summary,
        "break_min_confidence": cfg.break_min_confidence,
        "destinations": {"hospitals": len(hospitals), "towns": len(towns),
                         "outside": "roads leaving the area + "
                                    f"{ctx.config.osm.network_buffer_m / 1000:g} km buffer"},
        "cut_off_settlements": [
            {"settlement_id": r["settlement_id"], "name": r["name"],
             "buildings": int(r["building_count"]) if pd.notna(r["building_count"]) else None,
             "sentence": r["sentence"], "on_foot": r["all_ways_any"],
             **{k: v for k, v in rows[r["settlement_id"]].items() if k in ("lon", "lat")},
             "by_road": rows[r["settlement_id"]].get("motorable", {})}
            for _, r in cut.head(n).iterrows()],
        "key_breaks": key_breaks.drop(columns="geometry").head(n).to_dict("records")
        if not key_breaks.empty else [],
        "status_wording": "estimate from satellite change + pre-event OSM; verify on the ground",
        "meaning": {
            "likely cut off": "every pre-event road route to the nearest town, hospital or road "
                              "leaving the area crosses detected flood/debris of at least "
                              f"{cfg.break_min_confidence} confidence; the road is LIKELY, not "
                              "confirmed, impassable: needs field verification",
            "possibly cut off": "the same, but only with low-confidence detections",
            "detour": "still connected, but the shortest unaffected route is much longer",
            "connected": "no detected flood/debris on the route (undetected damage possible)",
            "no access before the event": "no mapped road within reach before the event",
            "outside": "a road leaving the area + network buffer is assumed to continue to the "
                       "wider network (towns and hospitals beyond); roads in the buffer have "
                       "no flood map and are assumed intact"},
    }
    outputs["files"] = {**files, "png": _render(ctx, roads, graphs, table, hospitals,
                                                key_breaks, outputs, out)}
    m = summary["motorable"]
    msg = (f"{m['likely cut off']} of {len(table):,} settlements likely cut off by road "
           f"({m['buildings_in_cut_off_settlements']:,} buildings; needs field verification), "
           f"{m['possibly cut off']} possibly, {m['detour']} with long detours; on foot: "
           f"{summary['all_ways']['likely cut off']} likely cut off; "
           f"{m['lost_hospital_access']} likely lost road access to every hospital")
    detail = out / "cutoff_detail.json"  # full per-settlement detail (priority stage, M5)
    detail.write_text(json.dumps(rows, default=str), encoding="utf-8")
    outputs["files"]["detail"] = detail
    return StageResult("cutoff", StageStatus.PARTIAL if warnings else StageStatus.OK, msg,
                       outputs=outputs, warnings=warnings)


def _sentence(name: str, rec: dict) -> str:
    """One plain sentence per settlement. Never claims certainty: a road crossing detected
    flood/debris is "likely impassable", and every isolation needs field verification."""
    mot, foot = rec.get("motorable", {}), rec.get("all_ways", {})
    m_any = mot.get("any", {})
    status = m_any.get("status", "no access before the event")
    if status == "likely cut off":
        hosp = mot.get("hospital", {})
        dest = (hosp.get("destination_before") if hosp.get("status") == "likely cut off"
                else m_any.get("destination_before"))
        text = f"{name}: likely cut off by road from {dest or 'the nearest town or hospital'}"
        brk = mot.get("break")
        if brk and brk["distance_km"] < 0.1:
            text += (f"; its own road ({brk['road']}) lies in detected flood/debris "
                     f"({brk['confidence']} confidence)")
        elif brk:
            text += (f"; road likely impassable at {brk['road']}, {brk['distance_km']:.1f} km "
                     f"away (flood/debris detected, {brk['confidence']} confidence)")
        f_status = foot.get("any", {}).get("status")
        text += ". " + {"likely cut off": "No mapped footpath route avoids it either.",
                        "connected": "Still reachable on foot/trails.",
                        "detour": "Still reachable on foot/trails (longer route).",
                        "possibly cut off": "On foot: possibly cut off.",
                        }.get(f_status, "No mapped footpath to compare.")
        return text.strip() + " Needs field verification."
    if status == "possibly cut off":
        return (f"{name}: possibly cut off by road (only low-confidence flood/debris on the "
                "route). Needs field verification.")
    if status == "detour":
        return (f"{name}: likely still connected by road, but if the flood/debris sections "
                f"are impassable the trip to {m_any.get('destination_after')} grows from "
                f"{m_any.get('before_km')} km to {m_any.get('after_km')} km.")
    if status == "connected":
        return (f"{name}: no flood/debris detected on its road connection to "
                f"{m_any.get('destination_after')} (undetected damage is still possible).")
    return f"{name}: no mapped road connection before the event (trail access only or OSM gap)."


def _key_breaks(items, roads, table, epsg) -> gpd.GeoDataFrame:
    if not items:
        return gpd.GeoDataFrame(columns=["road", "osm_id", "is_bridge", "settlements_cut_off",
                                         "buildings_cut_off", "lon", "lat"], geometry=[],
                                crs=4326)
    bcount = dict(zip(table["settlement_id"], table["building_count"].fillna(0), strict=True))
    df = pd.DataFrame([{"sid": s, "way": w, "x": m[0], "y": m[1]} for s, w, m in items])
    agg = df.groupby("way").agg(settlements_cut_off=("sid", "count"),
                                buildings_cut_off=("sid", lambda s: int(sum(bcount[i]
                                                                            for i in s))),
                                x=("x", "first"), y=("y", "first")).reset_index()
    pts = gpd.GeoSeries(gpd.points_from_xy(agg["x"], agg["y"]), crs=epsg).to_crs(4326)
    gdf = gpd.GeoDataFrame({
        "road": [_road_label(roads.loc[w]) for w in agg["way"]],
        "osm_id": [roads.loc[w, "osm_id"] for w in agg["way"]],
        "is_bridge": [bool(roads.loc[w, "is_bridge"]) for w in agg["way"]],
        "settlements_cut_off": agg["settlements_cut_off"].astype(int),
        "buildings_cut_off": agg["buildings_cut_off"].astype(int),
        "lon": pts.x.round(5), "lat": pts.y.round(5)}, geometry=pts.values, crs=4326)
    return gdf.sort_values(["buildings_cut_off", "settlements_cut_off"], ascending=False)


def _save_breaks(gdf, path) -> None:
    if gdf.empty:
        path.write_text('{"type": "FeatureCollection", "features": []}', encoding="utf-8")
        return
    gdf.assign(is_bridge=gdf["is_bridge"].astype(int)).to_file(path, driver="GeoJSON")


STATUS_COLORS = {"likely cut off": "#d7191c", "possibly cut off": "#fdae61", "detour": "#ffd400",
                 "connected": "#1a9641", "no access before the event": "#969696"}


def _render(ctx, roads, graphs, table, hospitals, key_breaks, outputs, out):
    from floodmap.stages.confidence import _read_band_by_name
    from floodmap.viz import crop_window, draw_lines, hillshade, render_flood_map

    conf_out = ctx.results.stages["confidence"].outputs
    terr = ctx.results.stages["terrain"].outputs
    with rasterio.open(conf_out["files"]["flood_map"]) as src:
        final, conf = src.read(1), src.read(2)
        transform, crs, shape = src.transform, src.crs, src.shape
    dem = _read_band_by_name(terr["files"]["hydrology"], "dem_m", shape, transform, crs, False)
    shade = hillshade(dem, abs(transform.a))
    breaks_utm = key_breaks.to_crs(ctx.aoi.utm_epsg) if not key_breaks.empty else key_breaks

    def overlay(ax):
        draw_lines(ax, roads.geometry[~roads["motorable"]], colors="#9e9e9e", linewidths=0.3)
        draw_lines(ax, roads.geometry[roads["motorable"]], colors="#404040", linewidths=0.6)
        handles = []
        for status, color in STATUS_COLORS.items():
            sel = table[table["motorable_any"] == status]
            size = 8 if status == "connected" else 26
            if not sel.empty:
                ax.scatter(sel.geometry.x / 1000, sel.geometry.y / 1000, s=size, color=color,
                           edgecolors="black", linewidths=0.3, zorder=5)
            handles.append(Line2D([], [], marker="o", ls="", color=color, mec="black", ms=6,
                                  label=f"settlement: {status} (by road)"))
        if not breaks_utm.empty:
            ax.scatter(breaks_utm.geometry.x / 1000, breaks_utm.geometry.y / 1000, marker="X",
                       s=70, color="#d7191c", edgecolors="black", linewidths=0.8, zorder=6)
            handles.append(Line2D([], [], marker="X", ls="", color="#d7191c", mec="black",
                                  ms=8, label="road/bridge break cutting settlements off"))
        if not hospitals.empty:
            ax.scatter(hospitals.geometry.x / 1000, hospitals.geometry.y / 1000, marker="P",
                       s=70, color="white", edgecolors="#006d2c", linewidths=1.2, zorder=6)
            handles.append(Line2D([], [], marker="P", ls="", color="white", mec="#006d2c",
                                  ms=8, label="hospital"))
        top = table[table["motorable_any"] == "likely cut off"].sort_values("building_count",
                                                                     ascending=False)
        for _, r in top.head(10).iterrows():
            if isinstance(r["name"], str) and r["name"].isascii():
                ax.annotate(r["name"], (r.geometry.x / 1000, r.geometry.y / 1000), fontsize=6,
                            xytext=(4, 4), textcoords="offset points", zorder=7,
                            bbox={"facecolor": "white", "alpha": 0.7, "lw": 0, "pad": 1})
        return handles

    m, f = outputs["by_network"]["motorable"], outputs["by_network"]["all_ways"]
    info = "\n".join([
        f"Settlements assessed: {outputs['settlements_assessed']:,}",
        f"Breaks counted at confidence >= {outputs['break_min_confidence']}",
        "",
        "By road (motorable):",
        *[f"  {k:<26} {m[k]:>6,}" for k in STATUSES],
        f"  buildings cut off          {m['buildings_in_cut_off_settlements']:>6,}",
        f"  lost all hospital access   {m['lost_hospital_access']:>6,}",
        "",
        "On foot (all ways):",
        f"  cut off                    {f['likely cut off']:>6,}",
        "",
        "Key breaks (buildings cut off):",
        *[f"  {b['road'][:24]:<24} {b['buildings_cut_off']:>6,}"
          for b in outputs["key_breaks"][:8]],
        "",
        "Destinations: nearest town, hospital",
        "or a road leaving the area.",
        "Estimate; verify on the ground.",
    ])
    title = f"Who is cut off? — event {ctx.event_date} — {ctx.results.event_name}"
    pngs = [render_flood_map(shade, final, conf, transform, title, info, out / "cutoff_map.png",
                             overlay=overlay)]
    if ctx.focus_bbox:
        win = crop_window(transform, shape, ctx.focus_bbox, ctx.aoi.utm_epsg)
        if win:
            pngs.append(render_flood_map(shade, final, conf, transform, title + " (focus)",
                                         info, out / "cutoff_map_focus.png", win,
                                         overlay=overlay))
    return pngs
