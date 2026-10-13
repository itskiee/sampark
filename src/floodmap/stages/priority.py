"""Stage "priority": rescue priority ranking of settlements.

Transparent tiers instead of an opaque weighted score, so every rank can be explained:
  1  cut off by road AND potentially affected buildings   (needs air/foot access + help)
  2  cut off by road, no damage detected                  (isolated: supplies, check-in)
  3  potentially affected buildings, still reachable by road (road teams can go)
  4  possibly cut off (low-confidence breaks only) or long detour
Within a tier: more potentially affected buildings (high confidence first), then more
buildings in total (the brief's population proxy: pre-event OSM building count).
Each entry carries its reasons, which the situation report and dashboard quote.
Outputs: priority/priority.csv (for field teams), priority/priority.geojson.
"""

from __future__ import annotations

import csv
import json

import geopandas as gpd
import pandas as pd

from floodmap.attribution import write_osm_license
from floodmap.pipeline import StageContext, register_stage
from floodmap.results import StageResult, StageStatus

TIER_TEXT = {1: "likely cut off by road and buildings potentially affected",
             2: "likely cut off by road",
             3: "buildings potentially affected, still reachable by road",
             4: "possibly cut off or long detour"}


def tier_of(status_road: str, affected: int) -> int | None:
    if status_road == "likely cut off":
        return 1 if affected > 0 else 2
    if affected > 0:
        return 3
    if status_road in ("possibly cut off", "detour"):
        return 4
    return None


def reasons_of(row, rec: dict, affected: int, high: int) -> list[str]:
    out = []
    road = rec.get("motorable", {})
    status = road.get("any", {}).get("status")
    if status == "likely cut off":
        brk = road.get("break")
        if brk and brk["distance_km"] < 0.1:
            out.append(f"likely cut off by road (its own road, {brk['road']}, lies in "
                       f"{brk['confidence']}-confidence flood/debris)")
        else:
            out.append("likely cut off by road" + (
                f" (likely impassable at {brk['road']}, {brk['distance_km']} km away, "
                f"{brk['confidence']} confidence)" if brk else ""))
    elif status in ("possibly cut off", "detour"):
        out.append(f"{status} by road")
    if road.get("hospital", {}).get("status") == "likely cut off":
        out.append("likely lost road access to hospital "
                   f"{road['hospital'].get('destination_before')}")
    foot = rec.get("all_ways", {}).get("any", {}).get("status")
    if foot == "likely cut off":
        out.append("no mapped footpath route avoids it either")
    if affected:
        out.append(f"{affected} buildings potentially affected ({high} high confidence)")
    if row.get("on_flood_corridor"):
        out.append("on the traced flood corridor")
    return out


@register_stage("priority", requires=("cutoff", "damage"))
def run_priority(ctx: StageContext) -> StageResult:
    cut = ctx.results.stages["cutoff"].outputs
    table = gpd.read_file(cut["files"]["settlements"])
    detail = json.loads(cut["files"]["detail"].read_text(encoding="utf-8"))
    dmg = gpd.read_file(ctx.results.stages["damage"].outputs["files"]["buildings"])
    sid = dmg["settlement_id"] if "settlement_id" in dmg else pd.Series(dtype=object)
    affected = sid[sid.fillna("") != ""].value_counts()
    high = sid[(sid.fillna("") != "") & (dmg.get("confidence") == "high")].value_counts() \
        if not dmg.empty else pd.Series(dtype=int)
    corridor_ids = set()
    sett_dmg = ctx.results.stages["damage"].outputs["files"]["settlements"]
    s_aff = gpd.read_file(sett_dmg)
    if "on_flood_corridor" in s_aff:
        corridor_ids = set(s_aff.loc[s_aff["on_flood_corridor"].astype(bool), "settlement_id"])

    entries = []
    for _, r in table.iterrows():
        s = r["settlement_id"]
        a, h = int(affected.get(s, 0)), int(high.get(s, 0))
        tier = tier_of(r["motorable_any"], a)
        if tier is None:
            continue
        rec = detail.get(s, {})
        row = {"on_flood_corridor": s in corridor_ids}
        entries.append({
            "tier": tier, "settlement_id": s, "name": r["name"],
            "buildings": int(r["building_count"]) if pd.notna(r["building_count"]) else 0,
            "potentially_affected_buildings": a, "of_which_high_confidence": h,
            "road_status": r["motorable_any"], "foot_status": r["all_ways_any"],
            "hospital_by_road": r["motorable_hospital"],
            "reasons": reasons_of(row, rec, a, h),
            "lon": rec.get("lon"), "lat": rec.get("lat"),
        })
    entries.sort(key=lambda e: (e["tier"], -e["of_which_high_confidence"],
                                -e["potentially_affected_buildings"], -e["buildings"]))
    for i, e in enumerate(entries, start=1):
        e["rank"] = i

    out = ctx.results.run_dir / "priority"
    out.mkdir(parents=True, exist_ok=True)
    write_osm_license(out)
    csv_path, geo_path = out / "priority.csv", out / "priority.geojson"
    fields = ["rank", "tier", "settlement_id", "name", "buildings",
              "potentially_affected_buildings", "of_which_high_confidence", "road_status",
              "foot_status", "hospital_by_road", "reasons", "lon", "lat"]
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for e in entries:
            writer.writerow({**{k: e[k] for k in fields}, "reasons": "; ".join(e["reasons"])})
    if entries:
        gdf = gpd.GeoDataFrame([{**e, "reasons": "; ".join(e["reasons"])} for e in entries],
                               geometry=gpd.points_from_xy([e["lon"] for e in entries],
                                                           [e["lat"] for e in entries]),
                               crs=4326)
        gdf.to_file(geo_path, driver="GeoJSON")
    else:
        geo_path.write_text('{"type": "FeatureCollection", "features": []}', encoding="utf-8")

    by_tier = {f"tier {t}: {txt}": sum(e["tier"] == t for e in entries)
               for t, txt in TIER_TEXT.items()}
    n = ctx.config.cutoff.max_listed
    msg = (f"{len(entries)} settlements ranked; " +
           ", ".join(f"tier {t}: {sum(e['tier'] == t for e in entries)}" for t in TIER_TEXT))
    return StageResult("priority", StageStatus.OK, msg, outputs={
        "ranked": len(entries), "by_tier": by_tier, "top": entries[:n],
        "verification": "all statuses are satellite-based estimates; verify on the ground",
        "method": "tiers (cut off + damage > cut off > damage > possibly cut off/detour), "
                  "then potentially affected buildings (high confidence first), then "
                  "building count (population proxy)",
        "files": {"csv": csv_path, "geojson": geo_path}})
