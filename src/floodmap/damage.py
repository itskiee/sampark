"""Overlay pre-event OSM assets with the flood/debris map → "potentially affected".

Every result is an ESTIMATE from satellite change detection, never a damage assessment:
an asset is "potentially affected" when it touches a detected flood/debris area. It
inherits that area's class and confidence (if several areas touch it: the highest
confidence and the most hazardous class).

Small buffers tolerate the radar pixel size (20 m) and small misregistration between OSM
and the satellite grid. Bridges get special treatment (proposal P8): the river under a
bridge was already water before the event, so a destroyed bridge often shows no change
ON the deck. We therefore look at the deck AND its approaches, and at the river reach
just up/downstream.
"""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
import shapely

from floodmap.flood_map import CHANGE_CLASSES, FINAL_CLASS_NAMES

CONF_RANK = {"low": 1, "medium": 2, "high": 3}
RANK_CONF = {v: k for k, v in CONF_RANK.items()}
# Most hazardous first (same rule as the confidence stage): water > wet mud > rough debris.
CLASS_PRIORITY = [FINAL_CLASS_NAMES[c] for c in CHANGE_CLASSES]


def _empty_match() -> pd.DataFrame:
    return pd.DataFrame({"flood_class": pd.Series(dtype=object),
                         "confidence": pd.Series(dtype=object)})


def match_flood(assets: gpd.GeoDataFrame, flood: gpd.GeoDataFrame, buffer_m: float
                ) -> pd.DataFrame:
    """For each asset touching a flood polygon (after buffering the asset by `buffer_m`):
    its flood class and confidence. Index = asset index; untouched assets are absent."""
    if assets.empty or flood.empty:
        return _empty_match()
    probe = gpd.GeoDataFrame(geometry=assets.geometry.buffer(buffer_m) if buffer_m > 0
                             else assets.geometry, crs=assets.crs)
    hits = gpd.sjoin(probe, flood[["class", "confidence", "geometry"]], how="inner",
                     predicate="intersects")
    if hits.empty:
        return _empty_match()
    hits = hits.assign(rank=hits["confidence"].map(CONF_RANK).fillna(0),
                       prio=hits["class"].map({c: i for i, c in enumerate(CLASS_PRIORITY)}))
    grouped = hits.groupby(level=0)
    out = pd.DataFrame({
        "flood_class": grouped["prio"].min().map(dict(enumerate(CLASS_PRIORITY))),
        "confidence": grouped["rank"].max().astype(int).map(RANK_CONF),
    })
    return out


def affected_buildings(buildings: gpd.GeoDataFrame, flood: gpd.GeoDataFrame,
                       buffer_m: float) -> gpd.GeoDataFrame:
    match = match_flood(buildings, flood, buffer_m)
    return buildings.loc[match.index].join(match)


def affected_roads(roads: gpd.GeoDataFrame, flood: gpd.GeoDataFrame, buffer_m: float
                   ) -> gpd.GeoDataFrame:
    """Affected road ways with the length inside detected flood/debris (`affected_m`) and
    the affected pieces as geometry (used by the cut-off analysis in M4)."""
    match = match_flood(roads, flood, buffer_m)
    if match.empty:
        cols = [*roads.columns, "flood_class", "confidence", "affected_m"]
        return gpd.GeoDataFrame(columns=cols, geometry="geometry", crs=roads.crs)
    hit = roads.loc[match.index].join(match)
    flood_zone = shapely.union_all(flood.geometry.buffer(buffer_m).values)
    pieces = shapely.intersection(hit.geometry.values, flood_zone)
    hit = hit.set_geometry(gpd.GeoSeries(pieces, index=hit.index, crs=roads.crs))
    hit["affected_m"] = hit.geometry.length.round(1)
    return hit[hit["affected_m"] > 0]


def assess_bridges(bridges: gpd.GeoDataFrame, flood: gpd.GeoDataFrame, approach_m: float,
                   reach_m: float) -> gpd.GeoDataFrame:
    """Bridges that are potentially affected, with the evidence:
    'deck/approach'  flood/debris within `approach_m` of the bridge line;
    'river reach'    flood/debris within `reach_m` (changed reach up/downstream)."""
    near = match_flood(bridges, flood, approach_m)
    reach = match_flood(bridges, flood, reach_m).drop(index=near.index, errors="ignore")
    parts = []
    if not near.empty:
        parts.append(bridges.loc[near.index].join(near).assign(evidence="deck/approach"))
    if not reach.empty:
        parts.append(bridges.loc[reach.index].join(reach).assign(evidence="river reach"))
    if not parts:
        return gpd.GeoDataFrame(columns=[*bridges.columns, "flood_class", "confidence",
                                         "evidence"], geometry="geometry", crs=bridges.crs)
    return gpd.GeoDataFrame(pd.concat(parts), crs=bridges.crs)


def affected_points(points: gpd.GeoDataFrame, flood: gpd.GeoDataFrame, buffer_m: float
                    ) -> gpd.GeoDataFrame:
    match = match_flood(points, flood, buffer_m)
    return points.loc[match.index].join(match)


def count_by(frame: pd.DataFrame, column: str, order: list[str]) -> dict[str, int]:
    counts = frame[column].value_counts() if not frame.empty else {}
    return {k: int(counts.get(k, 0)) for k in order}
