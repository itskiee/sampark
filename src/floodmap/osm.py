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
             "amenity", "healthcare", "ref", "access", "motor_vehicle")
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
    # unnamed facilities get coordinates: "unnamed hospital" tells a rescuer nothing
    where = health.geometry.to_crs(4326) if not health.empty else health.geometry
    health["label"] = [display_name(r, f"unnamed {c} at {p.y:.4f}°N {p.x:.4f}°E")
                       for (_, r), c, p in zip(health.iterrows(), health["category"], where,
                                               strict=True)]
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


def building_groups(xy: np.ndarray, link_m: float, max_extent_m: float) -> np.ndarray:
    """Group label per building: buildings within `link_m` of each other form one group
    (connected components of the "near each other" graph). Groups wider than
    `max_extent_m` are split on a grid of half that size, so a long chain of houses along a
    valley never becomes one 10 km "settlement"."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    from scipy.spatial import cKDTree

    n = len(xy)
    if n == 0:
        return np.zeros(0, dtype=np.int64)
    pairs = cKDTree(xy).query_pairs(link_m, output_type="ndarray")
    graph = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(n, n))
    _, labels = connected_components(graph, directed=False)
    out = labels.astype(np.int64)
    next_label = int(out.max()) + 1
    cell = max_extent_m / 2
    for lab in np.unique(labels):
        members = np.flatnonzero(labels == lab)
        pts = xy[members]
        if max(np.ptp(pts[:, 0]), np.ptp(pts[:, 1])) <= max_extent_m:
            continue
        keys = np.floor(pts / cell).astype(np.int64)
        _, sub = np.unique(keys, axis=0, return_inverse=True)
        out[members] = next_label + sub.ravel()
        next_label += int(sub.max()) + 1
    return out


def make_settlements(buildings: gpd.GeoDataFrame, places: gpd.GeoDataFrame, aoi_utm,
                     link_m: float, min_buildings: int, max_extent_m: float,
                     name_radius_m: float, near_radius_m: float
                     ) -> tuple[gpd.GeoDataFrame, np.ndarray]:
    """Settlements (proposal P18): groups of pre-event OSM buildings, named from OSM places.

    Returns (settlements, settlement_id per building; "" for isolated houses). Each
    settlement: id, display name, how it was named, place type, building count (the brief's
    population proxy) and a point on its central building. Named OSM places inside the area
    with no mapped buildings nearby are kept too (buildings 0), so an unmapped village is
    not silently dropped.
    """
    cols = ["settlement_id", "name", "named_from", "place", "building_count"]
    centroids = buildings.geometry.centroid
    xy = np.column_stack([centroids.x, centroids.y]) if len(buildings) else np.zeros((0, 2))
    labels = building_groups(xy, link_m, max_extent_m)
    counts = np.bincount(labels) if len(labels) else np.zeros(0, dtype=int)
    keep = [lab for lab in range(len(counts)) if counts[lab] >= min_buildings]

    reps, sizes = [], []
    for lab in keep:  # representative point: the building nearest the group's mean
        members = np.flatnonzero(labels == lab)
        centre = xy[members].mean(axis=0)
        reps.append(xy[members[np.argmin(((xy[members] - centre) ** 2).sum(axis=1))]])
        sizes.append(int(counts[lab]))
    groups = gpd.GeoDataFrame({"group": keep, "building_count": sizes},
                              geometry=gpd.points_from_xy([p[0] for p in reps],
                                                          [p[1] for p in reps]),
                              crs=buildings.crs if len(buildings) else places.crs)
    groups["name"], groups["named_from"], groups["place"] = None, "coordinates", None

    named = places[places.geometry.within(aoi_utm)] if not places.empty else places
    if not named.empty:  # only places with a real name can name a settlement
        named = named[named["name"].notna() | named["name_en"].notna()]
    if not groups.empty and not named.empty:
        # 1) each named place gives its name to the nearest group within name_radius_m
        near = gpd.sjoin_nearest(named[["label", "place", "geometry"]], groups[["geometry"]],
                                 how="inner", max_distance=name_radius_m, distance_col="d")
        near = near.sort_values("d").drop_duplicates("index_right")
        for _, r in near.iterrows():
            groups.loc[r["index_right"], ["name", "named_from", "place"]] = [
                r["label"], "osm place", r["place"]]
        # 2) other groups: "near <place>" within near_radius_m
        rest = groups[groups["name"].isna()]
        if not rest.empty:
            near2 = gpd.sjoin_nearest(rest[["geometry"]], named[["label", "geometry"]],
                                      how="inner", max_distance=near_radius_m)
            near2 = near2[~near2.index.duplicated(keep="first")]
            groups.loc[near2.index, "name"] = ["near " + v for v in near2["label"]]
            groups.loc[near2.index, "named_from"] = "nearest osm place"
        # 3) named places without any group nearby: unmapped buildings, keep as settlement
        orphan = named[~named.index.isin(near.index)]
        if not orphan.empty:
            extra = gpd.GeoDataFrame({"group": -1, "building_count": 0, "name": orphan["label"],
                                      "named_from": "osm place (no buildings mapped)",
                                      "place": orphan["place"]},
                                     geometry=orphan.geometry.values, crs=groups.crs)
            groups = gpd.GeoDataFrame(pd.concat([groups, extra], ignore_index=True),
                                      crs=groups.crs)
    elif groups.empty and not named.empty:
        groups = gpd.GeoDataFrame({"group": -1, "building_count": 0, "name": named["label"],
                                   "named_from": "osm place (no buildings mapped)",
                                   "place": named["place"]},
                                  geometry=named.geometry.values, crs=named.crs)

    if groups.empty:
        empty = gpd.GeoDataFrame({c: [] for c in cols}, geometry=[], crs=places.crs)
        return empty, np.full(len(buildings), "", dtype=object)
    lonlat = groups.geometry.to_crs(4326)
    unnamed = groups["name"].isna()
    groups.loc[unnamed, "name"] = [f"settlement at {p.y:.4f}°N {p.x:.4f}°E"
                                   for p in lonlat[unnamed]]
    groups = groups.sort_values("building_count", ascending=False).reset_index(drop=True)
    groups["settlement_id"] = [f"S{i + 1:05d}" for i in range(len(groups))]
    id_of = {g: s for g, s in zip(groups["group"], groups["settlement_id"], strict=True)
             if g >= 0}
    per_building = np.array([id_of.get(int(lab), "") for lab in labels], dtype=object)
    return groups[[*cols, "geometry"]], per_building
