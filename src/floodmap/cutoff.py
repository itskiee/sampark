"""Who is cut off? Road-graph reachability before vs after the event.

Graph: every pre-event OSM road way is split into straight segments between its vertices;
vertices shared by two ways (OSM junction nodes) join them. Roads that cross without a
shared node (a bridge over a road) are correctly NOT connected. Built twice (proposal P9):
  motorable   roads a 4x4/truck can use ("road connection" in the brief)
  all ways    including footpaths, steps and trails ("reachable on foot")
A virtual OUTSIDE node is joined to every road node outside the downloaded network area:
such a road continues to the wider network, so it is not a dead end (P9).

After the event, segments touching detected flood/debris (at or above the configured
confidence) and potentially affected bridges are removed. For each settlement we compare
the shortest route to the nearest destination before and after, and for cut-off
settlements find the first broken segment on their old route (what broke, how far away).

Shortest paths use scipy.sparse.csgraph.dijkstra with all destinations as sources at once
(`min_only=True`): one pass gives every node its nearest destination, the distance and the
route back to it.
"""

from __future__ import annotations

from dataclasses import dataclass

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree

from floodmap.damage import CONF_RANK

OUTSIDE_EPS = 1e-3  # metres; csgraph ignores zero-weight sparse entries, so use a tiny weight


@dataclass
class RoadGraph:
    xy: np.ndarray  # (n_nodes, 2) road node coordinates (UTM); the OUTSIDE node is extra
    u: np.ndarray  # edge endpoints (node indices)
    v: np.ndarray
    length: np.ndarray  # metres
    way: np.ndarray  # index (label) of the road way in the roads GeoDataFrame; -1 = outside
    outside: int  # index of the virtual OUTSIDE node

    @property
    def n_nodes(self) -> int:
        return len(self.xy) + 1

    def segments(self) -> np.ndarray:
        real = self.way >= 0
        return shapely.linestrings(np.stack([self.xy[self.u[real]], self.xy[self.v[real]]],
                                            axis=1))


def build_graph(roads: gpd.GeoDataFrame, inside, densify_m: float | None = None) -> RoadGraph:
    """Graph from road ways (UTM). `inside` = polygon of the downloaded network area shrunk
    by a margin; nodes outside it are joined to the OUTSIDE node. `densify_m` adds vertices
    so no segment is longer than that (original junction vertices are kept)."""
    if roads.empty:
        return RoadGraph(np.zeros((0, 2)), *(np.zeros(0, dtype=np.int64),) * 2, np.zeros(0),
                         np.zeros(0, dtype=np.int64), 0)
    parts = roads.geometry.explode(index_parts=False)
    parts = parts[parts.geom_type == "LineString"]
    if densify_m:
        parts = gpd.GeoSeries(shapely.segmentize(parts.values, densify_m), index=parts.index)
    coords, part_idx = shapely.get_coordinates(parts.values, return_index=True)
    way_of_part = np.asarray(parts.index)
    keys = np.round(coords, 1)  # shared OSM nodes project to identical coordinates
    uniq, node = np.unique(keys, axis=0, return_inverse=True)
    node = node.ravel()
    same_part = part_idx[1:] == part_idx[:-1]
    u, v = node[:-1][same_part], node[1:][same_part]
    seg_len = np.hypot(*(coords[1:][same_part] - coords[:-1][same_part]).T)
    way = way_of_part[part_idx[:-1][same_part]]
    keep = u != v
    edges = pd.DataFrame({"a": np.minimum(u, v)[keep], "b": np.maximum(u, v)[keep],
                          "length": seg_len[keep], "way": way[keep]})
    edges = edges.sort_values("length").drop_duplicates(["a", "b"])  # parallel duplicates
    xy = uniq.astype(float)
    outside = len(xy)
    out_nodes = np.flatnonzero(~shapely.contains_xy(inside, xy[:, 0], xy[:, 1]))
    return RoadGraph(
        xy,
        np.concatenate([edges["a"].to_numpy(), out_nodes]),
        np.concatenate([edges["b"].to_numpy(), np.full(len(out_nodes), outside)]),
        np.concatenate([edges["length"].to_numpy(), np.full(len(out_nodes), OUTSIDE_EPS)]),
        np.concatenate([edges["way"].to_numpy(), np.full(len(out_nodes), -1)]),
        outside)


def edge_confidence(graph: RoadGraph, flood: gpd.GeoDataFrame, buffer_m: float,
                    bridge_rank: dict[int, int]) -> np.ndarray:
    """Per edge: highest confidence (0 none, 1 low, 2 medium, 3 high) of detected
    flood/debris touching the segment (after buffering by `buffer_m`), or of the bridge rule
    for bridge ways. This is evidence that the road MAY be impassable, not a confirmation:
    the output always says "likely" and asks for field verification."""
    rank = np.zeros(len(graph.u), dtype=np.int8)
    real = np.flatnonzero(graph.way >= 0)
    if len(real) and not flood.empty:
        segments = graph.segments()
        levels = flood["confidence"].map(CONF_RANK).fillna(0).astype(int)
        for level in (1, 2, 3):
            polys = flood[levels == level]
            if polys.empty:
                continue
            tree = shapely.STRtree(polys.geometry.buffer(buffer_m).values)
            seg_idx, _ = tree.query(segments, predicate="intersects")
            hit = real[np.unique(seg_idx)]
            rank[hit] = np.maximum(rank[hit], level)
    if bridge_rank:
        per_edge = pd.Series(graph.way).map(bridge_rank).fillna(0).to_numpy(dtype=np.int8)
        rank = np.maximum(rank, per_edge)
    return rank


def broken_edges(graph: RoadGraph, flood: gpd.GeoDataFrame, buffer_m: float,
                 broken_ways: set, min_confidence: str) -> np.ndarray:
    """Boolean per edge: likely impassable at `min_confidence` or above; `broken_ways`
    (bridges already judged at that level) always count."""
    rank = edge_confidence(graph, flood, buffer_m, dict.fromkeys(broken_ways, 3))
    return rank >= CONF_RANK[min_confidence]


def _csr(graph: RoadGraph, keep: np.ndarray) -> csr_matrix:
    n = graph.n_nodes
    return csr_matrix((graph.length[keep], (graph.u[keep], graph.v[keep])), shape=(n, n))


@dataclass
class Reach:
    dist: np.ndarray  # metres to the nearest destination (inf = unreachable)
    pred: np.ndarray  # predecessor towards that destination (-9999 at sources/unreached)
    source: np.ndarray  # destination node reached (-9999 if unreachable)


def reach(graph: RoadGraph, keep: np.ndarray, dest_nodes: list[int]) -> Reach:
    n = graph.n_nodes
    if not dest_nodes:
        return Reach(np.full(n, np.inf), np.full(n, -9999), np.full(n, -9999))
    dist, pred, src = dijkstra(_csr(graph, keep), directed=False,
                               indices=sorted(set(dest_nodes)), min_only=True,
                               return_predecessors=True)
    return Reach(dist, pred, src)


def snap(graph: RoadGraph, points: gpd.GeoSeries, max_m: float) -> np.ndarray:
    """Nearest road node for each point (-1 if none within `max_m`)."""
    if len(points) == 0 or len(graph.xy) == 0:
        return np.full(len(points), -1)
    d, idx = cKDTree(graph.xy).query(np.column_stack([points.x, points.y]),
                                     distance_upper_bound=max_m)
    return np.where(np.isfinite(d), idx, -1)


def edge_lookup(graph: RoadGraph) -> dict[tuple[int, int], int]:
    return {(int(a), int(b)) if a < b else (int(b), int(a)): i
            for i, (a, b) in enumerate(zip(graph.u, graph.v, strict=True))}


def first_break(graph: RoadGraph, before: Reach, start: int, broken: np.ndarray,
                lookup: dict[tuple[int, int], int]) -> tuple[int, float] | None:
    """Walk the pre-event route from `start` towards its destination; return the first
    broken edge on it and the distance (m) from the settlement to that break."""
    node, travelled = start, 0.0
    for _ in range(len(graph.xy) + 1):
        nxt = int(before.pred[node])
        if nxt < 0:
            return None
        e = lookup[(node, nxt) if node < nxt else (nxt, node)]
        if broken[e]:
            return e, travelled
        travelled += float(graph.length[e])
        node = nxt
    return None


def classify(d0: float, d1: float, d1_worst: float, factor: float, min_extra_m: float) -> str:
    """Status of one settlement for one destination set."""
    if not np.isfinite(d0):
        return "no access before the event"
    if not np.isfinite(d1):
        return "likely cut off"
    if not np.isfinite(d1_worst):
        return "possibly cut off"
    if d1 >= factor * d0 and d1 - d0 >= min_extra_m:
        return "detour"
    return "connected"
