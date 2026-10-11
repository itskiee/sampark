"""Stage "osm": pre-event OpenStreetMap from the ohsome API (the only allowed OSM source).

Snapshot = data_rules.ohsome_snapshot(event date, newest ohsome data): 00:00 UTC on the day
before the event, capped at the newest data ohsome holds. Downloads (cached per area and
snapshot):
  buildings  inside the area (population proxy, damage overlay)
  roads      area + network buffer (incl. bridges; cut-off analysis in M4)
  places, health facilities   area + network buffer
Outputs: osm/osm_pre_event.gpkg (all layers, UTM) and osm/settlements.geojson (places with
their building counts, WGS84).
"""

from __future__ import annotations

import logging

import geopandas as gpd
import pandas as pd

from floodmap.attribution import write_osm_license
from floodmap.data_rules import OHSOME_LATEST_KNOWN, ohsome_snapshot
from floodmap.ohsome_client import fetch_snapshot, latest_data_timestamp
from floodmap.osm import (
    BUILDINGS_FILTER,
    PLACE_TYPES,
    POIS_FILTER,
    ROADS_FILTER,
    features_to_gdf,
    prepare_roads,
    settlement_building_counts,
    split_pois,
)
from floodmap.pipeline import StageContext, register_stage
from floodmap.results import StageResult, StageStatus
from floodmap.s1_pairing import to_utm
from floodmap.terrain import buffered_bounds

log = logging.getLogger("floodmap.stages.osm")
RAW_LAYERS = ("buildings", "roads", "pois")


def _write_layers(path, layers: dict[str, gpd.GeoDataFrame]) -> None:
    path.unlink(missing_ok=True)
    for name, gdf in layers.items():
        gdf.to_file(path, layer=name, driver="GPKG")


def _read_layer(path, name: str) -> gpd.GeoDataFrame:
    return gpd.read_file(path, layer=name)


def download_raw(ctx: StageContext, snapshot) -> dict[str, gpd.GeoDataFrame]:
    """The three raw layers at the snapshot (from the cache when available)."""
    cfg, aoi, net = ctx.config.osm, ctx.aoi, ctx.config.network
    big_bbox = buffered_bounds(aoi, cfg.network_buffer_m)
    key = {"snapshot": snapshot.isoformat(), "bounds": aoi.bounds, "big_bbox": big_bbox,
           "filters": [BUILDINGS_FILTER, ROADS_FILTER, POIS_FILTER],
           "window_h": cfg.history_window_hours, "max_len": cfg.max_text_length,
           "epsg": aoi.utm_epsg}
    folder = ctx.cache.stage_dir(aoi.aoi_hash, ctx.event_date, "osm", key)
    gpkg = folder / "osm_raw.gpkg"
    if ctx.cache.is_complete(folder):
        return {name: _read_layer(gpkg, name) for name in RAW_LAYERS}

    ctx.cache.prepare(folder)
    kw = {"timeout_s": cfg.timeout_s, "retries": net.retries,
          "window_hours": cfg.history_window_hours}
    jobs = (("buildings", BUILDINGS_FILTER, aoi.bounds, cfg.max_tile_km2_buildings),
            ("roads", ROADS_FILTER, big_bbox, cfg.max_tile_km2_sparse),
            ("pois", POIS_FILTER, big_bbox, cfg.max_tile_km2_sparse))
    layers = {}
    for name, osm_filter, bbox, tile_km2 in jobs:
        ctx.progress("osm", f"downloading pre-event OSM {name} (snapshot {snapshot:%Y-%m-%d})")
        feats = fetch_snapshot(osm_filter, bbox, snapshot, max_tile_km2=tile_km2, **kw)
        layers[name] = features_to_gdf(feats, aoi.utm_epsg, cfg.max_text_length)
        log.info("OSM %s: %d features", name, len(layers[name]))
    _write_layers(gpkg, layers)
    ctx.cache.mark_complete(folder, {**key, "counts": {k: len(v) for k, v in layers.items()}})
    return layers


@register_stage("osm")
def run_osm(ctx: StageContext) -> StageResult:
    cfg, aoi = ctx.config.osm, ctx.aoi
    warnings: list[str] = []
    try:
        latest = latest_data_timestamp(timeout_s=ctx.config.network.timeout_s,
                                       retries=ctx.config.network.retries)
    except Exception as exc:  # metadata only sets the cap; fall back to the known value
        latest = OHSOME_LATEST_KNOWN
        warnings.append(f"ohsome metadata unavailable ({type(exc).__name__}); "
                        f"assumed newest data {latest:%Y-%m-%d}")
    snapshot = ohsome_snapshot(ctx.event_date, latest)

    raw = download_raw(ctx, snapshot)
    aoi_utm = to_utm(aoi.geometry, aoi.utm_epsg)
    buildings = raw["buildings"]
    if not buildings.empty:  # bbox download → keep buildings inside the actual area
        buildings = buildings[buildings.geometry.intersects(aoi_utm)]
    roads = prepare_roads(raw["roads"], cfg.motorable_highways)
    poi = split_pois(raw["pois"])
    places, unassigned = settlement_building_counts(poi["places"], buildings,
                                                    cfg.settlement_radius_m, aoi_utm)
    health = poi["health"]

    if buildings.empty:
        warnings.append("no buildings mapped in OSM here: building counts are 0 because of "
                        "OSM completeness, not because nothing was hit")
    if not (health["category"] == "hospital").any():
        warnings.append("no hospital mapped in OSM within the area + network buffer")
    if roads.empty:
        warnings.append("no roads mapped in OSM here")

    out = ctx.results.run_dir / "osm"
    out.mkdir(parents=True, exist_ok=True)
    write_osm_license(out)
    gpkg = out / "osm_pre_event.gpkg"
    _write_layers(gpkg, {"buildings": buildings, "roads": roads, "places": places,
                         "health": health})
    settlements = out / "settlements.geojson"
    places.to_crs(4326).to_file(settlements, driver="GeoJSON")

    in_aoi_roads = roads[roads.geometry.intersects(aoi_utm)]
    bridges = in_aoi_roads[in_aoi_roads["is_bridge"]]
    place_counts = places["place"].value_counts()
    msg = (f"snapshot {snapshot:%Y-%m-%d %H:%M}Z: {len(buildings):,} buildings, "
           f"{in_aoi_roads.length.sum() / 1000:,.0f} km of roads/paths in the area "
           f"({len(bridges)} bridges), {len(places)} places, "
           f"{int((health['category'] == 'hospital').sum())} hospitals")
    return StageResult("osm", StageStatus.PARTIAL if warnings else StageStatus.OK, msg,
                       warnings=warnings, outputs={
        "snapshot_utc": snapshot.isoformat(),
        "method": "ohsome v1 full history, versions valid at the snapshot",
        "buildings": len(buildings),
        "buildings_outside_named_places": unassigned,
        "roads_in_area_km": {
            "motorable": round(float(in_aoi_roads.loc[in_aoi_roads["motorable"]].length.sum()
                                     / 1000), 1),
            "all_ways": round(float(in_aoi_roads.length.sum() / 1000), 1)},
        "bridges_in_area": {"total": len(bridges),
                            "motorable": int(bridges["motorable"].sum())},
        "places": {t: int(place_counts.get(t, 0)) for t in PLACE_TYPES},
        "hospitals": int((health["category"] == "hospital").sum()),
        "other_health_facilities": int((health["category"] != "hospital").sum()),
        "network_buffer_m": cfg.network_buffer_m,
        "files": {"gpkg": gpkg, "settlements": settlements},
    })


def load_layers(osm_outputs: dict) -> dict[str, gpd.GeoDataFrame]:
    """Read the processed OSM layers written by this stage (used by later stages)."""
    gpkg = osm_outputs["files"]["gpkg"]
    layers = {name: _read_layer(gpkg, name) for name in
              ("buildings", "roads", "places", "health")}
    for name in ("roads",):
        for col in ("motorable", "is_bridge"):
            if col in layers[name]:
                layers[name][col] = layers[name][col].astype(bool)
    if "building_count" in layers["places"]:
        layers["places"]["building_count"] = pd.to_numeric(layers["places"]["building_count"],
                                                           errors="coerce")
    return layers
