"""Pre-event OpenStreetMap layers: buildings, roads (incl. bridges), places and health
facilities, as GeoDataFrames in the AOI's UTM projection.

Only the OSM content the brief's task names is used: buildings, roads and bridges
("What was damaged?"), and settlements, towns and hospitals ("Who is cut off?").

OSM tags are user-generated text. They are treated as untrusted DATA: only a fixed list of
tags is kept, control characters are removed and length is capped, because names later
reach the dashboard and the language model (prompt-injection defence).
The OSM `population` tag is deliberately NOT kept: the brief's population proxy is the
count of pre-event OSM buildings, nothing else.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import shape

# --- ohsome filters (ohsome filter language) -------------------------------------------
BUILDINGS_FILTER = "building=* and building!=no and geometry:polygon"
# Every way people or vehicles travel on; construction/proposed/abandoned ways are excluded.
ROADS_FILTER = ("highway=* and type:way and not highway in (proposed, construction, "
                "abandoned, razed, disused, platform, elevator, corridor, bus_stop, "
                "rest_area, services, raceway, escape, emergency_bay)")
PLACE_TYPES = ("city", "town", "village", "hamlet", "isolated_dwelling")
POIS_FILTER = (f"place in ({', '.join(PLACE_TYPES)}) or amenity in (hospital, clinic, "
               "doctors) or healthcare in (hospital, clinic, centre, doctor, health_post)")

# Tags we keep (everything else is dropped on arrival).
KEPT_TAGS = ("name", "name:en", "name:ne", "highway", "bridge", "building", "place",
             "amenity", "healthcare", "ref", "surface", "tracktype", "layer",
             "access", "motor_vehicle")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f​-‏‪-‮⁦-⁩]")


def clean_text(value: Any, max_len: int) -> str | None:
    """Untrusted OSM text → safe display/LLM string (no control or bidi characters)."""
    if value is None:
        return None
    text = unicodedata.normalize("NFC", str(value))
    text = re.sub(r"\s+", " ", text)  # line breaks/tabs → one space (before removal below)
    text = _CONTROL.sub("", text).strip()
    return text[:max_len] or None


def features_to_gdf(features: list[dict[str, Any]], epsg: int, max_len: int
                    ) -> gpd.GeoDataFrame:
    """ohsome GeoJSON features → GeoDataFrame (UTM) with osm_id + kept, cleaned tags."""
    rows, geoms = [], []
    for feat in features:
        geom = feat.get("geometry")
        if not geom:
            continue
        props = feat.get("properties") or {}
        row = {"osm_id": str(props.get("@osmId", ""))}
        for tag in KEPT_TAGS:
            row[tag.replace(":", "_")] = clean_text(props.get(tag), max_len)
        rows.append(row)
        geoms.append(shape(geom))
    columns = ["osm_id", *[t.replace(":", "_") for t in KEPT_TAGS]]
    gdf = gpd.GeoDataFrame(rows, columns=columns, geometry=geoms, crs=4326)
    if gdf.empty:
        return gdf.to_crs(epsg)
    gdf = gdf.to_crs(epsg)
    invalid = ~gdf.geometry.is_valid
    if invalid.any():  # self-intersecting footprints are common in OSM
        gdf.loc[invalid, "geometry"] = shapely.make_valid(gdf.geometry[invalid].values)
    return gdf


def display_name(row: dict[str, Any] | Any, fallback: str) -> str:
    """English name if mapped, else the local name, else a descriptive fallback."""
    for key in ("name_en", "name"):
        value = row.get(key) if hasattr(row, "get") else None
        if isinstance(value, str) and value:
            return value
    return fallback


def prepare_roads(roads: gpd.GeoDataFrame, motorable: tuple[str, ...]) -> gpd.GeoDataFrame:
    """Add `motorable`, `is_bridge` and `length_m` to the road layer."""
    roads = roads.copy()
    roads["motorable"] = roads["highway"].isin(motorable) & ~roads["motor_vehicle"].isin(
        ["no"]) & ~roads["access"].isin(["no", "private"]).fillna(False)
    roads["is_bridge"] = roads["bridge"].notna() & ~roads["bridge"].isin(["no"])
    roads["length_m"] = roads.geometry.length.round(1)
    return roads


def split_pois(pois: gpd.GeoDataFrame) -> dict[str, gpd.GeoDataFrame]:
    """Places and health facilities as point layers (polygons → centroid)."""
    pois = pois.copy()
    pois["geometry"] = pois.geometry.representative_point()
    is_place = pois["place"].isin(PLACE_TYPES)
    is_hospital = pois["amenity"].isin(["hospital"]) | pois["healthcare"].isin(["hospital"])
    is_health = is_hospital | pois["amenity"].isin(["clinic", "doctors"]) | pois[
        "healthcare"].isin(["clinic", "centre", "doctor", "health_post"])

    places = pois[is_place].copy()
    places["label"] = [display_name(r, f"unnamed {r['place'].replace('_', ' ')}")
                       for _, r in places.iterrows()]
    health = pois[is_health].copy()
    health["category"] = np.where(is_hospital[is_health], "hospital", "health facility")
    health["label"] = [display_name(r, f"unnamed {c}") for (_, r), c in
                       zip(health.iterrows(), health["category"], strict=True)]
    return {"places": places, "health": health}


def settlement_building_counts(places: gpd.GeoDataFrame, buildings: gpd.GeoDataFrame,
                               radius_m: float, aoi_utm) -> tuple[gpd.GeoDataFrame, int]:
    """Assign each building to the nearest place within `radius_m` and count them.

    The building count is the brief's population proxy. Places outside the analysed area
    get no count (their buildings were not downloaded), shown as None, not as 0.
    Returns (places with `building_count`, number of buildings not near any named place).
    """
    places = places.copy()
    places["in_aoi"] = places.geometry.within(aoi_utm)
    places["building_count"] = None
    if places.empty or buildings.empty:
        places.loc[places["in_aoi"], "building_count"] = 0
        return places, len(buildings)
    centroids = gpd.GeoDataFrame(geometry=buildings.geometry.centroid.values, crs=buildings.crs)
    joined = gpd.sjoin_nearest(centroids, places[["geometry"]], how="left",
                               max_distance=radius_m)
    joined = joined[~joined.index.duplicated(keep="first")]  # ties: keep one place
    counts = joined["index_right"].value_counts()
    places.loc[places["in_aoi"], "building_count"] = [
        int(counts.get(i, 0)) for i in places.index[places["in_aoi"]]]
    return places, int(joined["index_right"].isna().sum())


def nearest_place(points: gpd.GeoDataFrame, places: gpd.GeoDataFrame,
                  radius_m: float) -> list[int | None]:
    """Index (in `places`) of the nearest place within `radius_m` for each geometry."""
    if points.empty or places.empty:
        return [None] * len(points)
    pts = gpd.GeoDataFrame(geometry=points.geometry.centroid.values, crs=points.crs)
    joined = gpd.sjoin_nearest(pts, places[["geometry"]], how="left", max_distance=radius_m)
    joined = joined[~joined.index.duplicated(keep="first")]
    return [None if pd.isna(v) else int(v) for v in joined["index_right"]]
