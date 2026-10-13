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
import numpy as np
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


def road_pieces_in(lines: np.ndarray, zones: np.ndarray) -> np.ndarray:
    """Part of each line inside the union of `zones`.

    Same result as intersecting every line with one merged zone, but each line is cut by
    the merge of only the few zones it touches (merging polygons is numerically robust;
    merging overlapping line pieces is not: near-identical pieces from overlapping zones
    would be counted twice, which a test caught). Measured on Valencia 2024 (2,448 km of
    roads, thousands of flood polygons): 345 s with one merged zone.
    """
    out = np.array([shapely.LineString()] * len(lines), dtype=object)
    if len(lines) == 0 or len(zones) == 0:
        return out
    zones = subdivide(zones)
    li, zi = shapely.STRtree(zones).query(lines, predicate="intersects")
    order = np.argsort(li, kind="stable")
    li, zi = li[order], zi[order]
    starts = np.flatnonzero(np.r_[True, li[1:] != li[:-1]])
    for s, e in zip(starts, np.r_[starts[1:], len(li)], strict=True):
        out[li[s]] = shapely.intersection(lines[li[s]], shapely.union_all(zones[zi[s:e]]))
    return out


def subdivide(zones: np.ndarray, max_vertices: int = 256) -> np.ndarray:
    """Split every polygon with more than `max_vertices` into pieces that exactly tile it
    (halving its box again and again, like PostGIS ST_Subdivide).

    Why: a road must then be overlaid with a few small pieces, not with a whole huge flood
    polygon (Storm Daniel test 2026-10-13: 89,114 polygons, 1 million vertices, about 0.8 s
    per road and roughly an hour for the damage stage before). The pieces cover exactly the
    same area, so every result is unchanged (tests compare with the merged-zone method)."""
    out: list = []
    for z in zones:
        _halve(z, max_vertices, out, depth=0)
    return np.array(out, dtype=object)


def _halve(geom, max_vertices: int, out: list, depth: int) -> None:
    if geom.is_empty:
        return
    if shapely.get_num_coordinates(geom) <= max_vertices or depth >= 24:
        out.append(geom)
        return
    x0, y0, x1, y1 = geom.bounds
    if x1 - x0 >= y1 - y0:
        xm = (x0 + x1) / 2
        boxes = ((x0, y0, xm, y1), (xm, y0, x1, y1))
    else:
        ym = (y0 + y1) / 2
        boxes = ((x0, y0, x1, ym), (x0, ym, x1, y1))
    for b in boxes:
        try:
            part = shapely.clip_by_rect(geom, *b)
            if not part.is_empty and not part.is_valid:  # rectangle clipping can be "dirty"
                part = shapely.make_valid(part)
        except shapely.errors.GEOSException:  # degenerate input: exact (slower) overlay
            part = shapely.intersection(shapely.make_valid(geom), shapely.box(*b))
        _halve(part, max_vertices, out, depth + 1)


def affected_roads(roads: gpd.GeoDataFrame, flood: gpd.GeoDataFrame, buffer_m: float
                   ) -> gpd.GeoDataFrame:
    """Affected road ways with the length inside detected flood/debris (`affected_m`) and
    the affected pieces as geometry (used by the cut-off analysis in M4)."""
    match = match_flood(roads, flood, buffer_m)
    if match.empty:
        cols = [*roads.columns, "flood_class", "confidence", "affected_m"]
        return gpd.GeoDataFrame(columns=cols, geometry="geometry", crs=roads.crs)
    hit = roads.loc[match.index].join(match)
    hit = hit.set_geometry(gpd.GeoSeries(road_pieces_in(hit.geometry.values,
                                                        flood.geometry.buffer(buffer_m).values),
                                         index=hit.index, crs=roads.crs))
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
