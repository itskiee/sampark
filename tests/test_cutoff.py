"""Cut-off graph logic, using the brief's own example: after the event villages B and C
lose access to the district hospital, A and D keep it."""

from __future__ import annotations

import geopandas as gpd
import numpy as np
from shapely.geometry import LineString, Point, box

from floodmap.cutoff import (
    broken_edges,
    build_graph,
    classify,
    edge_lookup,
    first_break,
    reach,
    snap,
)

EPSG = 32645
#  Hospital H (0,0) ── A (1000,0) ── bridge (2000→2100) ── B (3000,0) ── C (4000,0)
#  H ── D (0,1000) via a separate road; a long mountain track joins C back to H via (4000,3000)
ROADS = gpd.GeoDataFrame(
    {"highway": ["primary", "primary", "primary", "primary", "secondary", "path"],
     "is_bridge": [False, True, False, False, False, False],
     "name": ["Main", "Big Bridge", "Main", "Main", "D road", "Ridge trail"]},
    geometry=[LineString([(0, 0), (1000, 0), (2000, 0)]),
              LineString([(2000, 0), (2100, 0)]),
              LineString([(2100, 0), (3000, 0)]),
              LineString([(3000, 0), (4000, 0)]),
              LineString([(0, 0), (0, 1000)]),
              LineString([(4000, 0), (4000, 3000), (0, 3000), (0, 1000)])],
    crs=EPSG)
INSIDE = box(-50_000, -50_000, 50_000, 50_000)  # no road leaves the area here
VILLAGES = gpd.GeoSeries([Point(1000, 10), Point(3000, 10), Point(4000, 10), Point(0, 1010)],
                         crs=EPSG)  # A, B, C, D


def _flood_on_bridge(conf="high"):
    return gpd.GeoDataFrame({"class": ["new water-like"], "confidence": [conf]},
                            geometry=[box(2020, -30, 2080, 30)], crs=EPSG)


def _run(roads, flood, min_conf="medium"):
    g = build_graph(roads, INSIDE)
    hosp = snap(g, gpd.GeoSeries([Point(0, 0)], crs=EPSG), 500).tolist()
    nodes = snap(g, VILLAGES, 500)
    keep_all = np.ones(len(g.u), dtype=bool)
    broken = broken_edges(g, flood, 10, set(), min_conf)
    before, after = reach(g, keep_all, hosp), reach(g, ~broken, hosp)
    return g, nodes, before, after, broken


def test_brief_example_motorable_network():
    motorable = ROADS[ROADS["highway"] != "path"]
    g, nodes, before, after, broken = _run(motorable, _flood_on_bridge())
    d0, d1 = before.dist[nodes], after.dist[nodes]
    assert np.isfinite(d0).all()
    status = [classify(a, b, b, 1.5, 2000) for a, b in zip(d0, d1, strict=True)]
    assert status == ["connected", "likely cut off", "likely cut off", "connected"]  # A, B, C, D
    # where did B's road break? the bridge, 900 m away along the road
    e, dist = first_break(g, before, int(nodes[1]), broken, edge_lookup(g))
    assert g.way[e] == 1 and abs(dist - 900) < 1


def test_footpath_gives_long_detour_on_all_ways_network():
    g, nodes, before, after, _ = _run(ROADS, _flood_on_bridge())
    c0, c1 = before.dist[nodes[2]], after.dist[nodes[2]]
    assert np.isfinite(c1) and c1 > c0  # C still reachable via the ridge trail
    assert classify(c0, c1, c1, 1.5, 2000) == "detour"


def test_low_confidence_flood_does_not_break_by_default():
    motorable = ROADS[ROADS["highway"] != "path"]
    _, nodes, _, after, broken = _run(motorable, _flood_on_bridge("low"))
    assert not broken.any() and np.isfinite(after.dist[nodes]).all()
    _, _, _, worst, _ = _run(motorable, _flood_on_bridge("low"), min_conf="low")
    assert classify(1, 1, worst.dist[nodes[1]], 1.5, 2000) == "possibly cut off"


def test_roads_leaving_the_area_reach_outside():
    g = build_graph(ROADS, box(-100, -100, 2500, 2500))  # C and the ridge are "outside"
    assert (g.v == g.outside).any()
    r = reach(g, np.ones(len(g.u), dtype=bool), [g.outside])
    assert np.isfinite(r.dist[snap(g, VILLAGES, 500)]).all()


def test_crossing_without_shared_node_is_not_a_junction():
    roads = gpd.GeoDataFrame({"highway": ["primary", "primary"]},
                             geometry=[LineString([(0, 0), (100, 0)]),
                                       LineString([(50, -50), (50, 50)])], crs=EPSG)
    g = build_graph(roads, INSIDE)
    r = reach(g, np.ones(len(g.u), dtype=bool), snap(g, gpd.GeoSeries([Point(0, 0)]), 5).tolist())
    far = snap(g, gpd.GeoSeries([Point(50, 50)]), 5)[0]
    assert not np.isfinite(r.dist[far])  # over/under-pass: no connection


def test_no_access_and_snapping():
    assert classify(np.inf, np.inf, np.inf, 1.5, 2000) == "no access before the event"
    g = build_graph(ROADS, INSIDE)
    assert snap(g, gpd.GeoSeries([Point(20_000, 20_000)]), 500)[0] == -1


def test_edge_confidence_matches_reference():
    """The fast query (segments in the tree, flood polygons as prepared queries) must give
    exactly the old result (polygons in the tree, segments as queries). Found 2026-10-13:
    the old order took > 40 min on the huge Storm Daniel flood polygon."""
    import shapely

    from floodmap.cutoff import CONF_RANK, edge_confidence

    rng = np.random.default_rng(7)
    starts = rng.uniform(0, 20_000, (400, 2))
    roads = gpd.GeoDataFrame(
        {"highway": ["primary"] * 400, "is_bridge": [False] * 400, "name": ["r"] * 400},
        geometry=[LineString([tuple(a), tuple(a + rng.uniform(-3000, 3000, 2))])
                  for a in starts], crs=EPSG)
    g = build_graph(roads, INSIDE, densify_m=50)
    # one big, jagged polygon (many vertices) plus small patches at every confidence level
    angles = np.linspace(0, 2 * np.pi, 4000, endpoint=False)
    radius = 6000 + 1500 * np.sin(angles * 37) + rng.uniform(0, 300, angles.size)
    big = shapely.Polygon(np.column_stack([10_000 + radius * np.cos(angles),
                                           10_000 + radius * np.sin(angles)]))
    small = [box(x, y, x + 200, y + 200) for x, y in rng.uniform(0, 20_000, (60, 2))]
    flood = gpd.GeoDataFrame(
        {"confidence": ["medium"] + list(rng.choice(["low", "medium", "high"], 60))},
        geometry=[big] + small, crs=EPSG)

    new = edge_confidence(g, flood, 10, {})

    old = np.zeros(len(g.u), dtype=np.int8)  # reference: the previous implementation
    real = np.flatnonzero(g.way >= 0)
    segments = g.segments()
    levels = flood["confidence"].map(CONF_RANK).fillna(0).astype(int)
    for level in (1, 2, 3):
        polys = flood[levels == level]
        tree = shapely.STRtree(polys.geometry.buffer(10).values)
        seg_idx, _ = tree.query(segments, predicate="intersects")
        hit = real[np.unique(seg_idx)]
        old[hit] = np.maximum(old[hit], level)

    assert (new > 0).sum() > 100  # the test really exercises hits
    assert np.array_equal(new, old)
