"""Damage overlay rules: buildings, roads, bridges (P8), facilities."""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point, box

from floodmap.damage import (
    affected_buildings,
    affected_points,
    affected_roads,
    assess_bridges,
    count_by,
    match_flood,
)

EPSG = 32645
WATER, MUD, DEBRIS = ("new water-like", "backscatter decrease (likely wet mud / debris)",
                      "backscatter increase (likely rough debris)")


@pytest.fixture
def flood():
    # two overlapping areas around x = 0..100 (low mud + high water), one debris field far east
    return gpd.GeoDataFrame(
        {"class": [MUD, WATER, DEBRIS], "confidence": ["low", "high", "medium"]},
        geometry=[box(0, 0, 100, 100), box(50, 0, 100, 100), box(1000, 0, 1100, 100)],
        crs=EPSG)


def test_highest_confidence_and_most_hazardous_class_win(flood):
    assets = gpd.GeoDataFrame(geometry=[box(60, 10, 70, 20), box(10, 10, 20, 20),
                                        box(500, 10, 510, 20)], crs=EPSG)
    m = match_flood(assets, flood, 0)
    assert m.loc[0, "confidence"] == "high" and m.loc[0, "flood_class"] == WATER
    assert m.loc[1, "confidence"] == "low" and m.loc[1, "flood_class"] == MUD
    assert 2 not in m.index


def test_building_buffer_tolerates_misregistration(flood):
    near = gpd.GeoDataFrame(geometry=[box(105, 10, 112, 17)], crs=EPSG)  # 5 m outside
    assert affected_buildings(near, flood, 0).empty
    hit = affected_buildings(near, flood, 10)
    assert len(hit) == 1 and hit.iloc[0]["confidence"] == "high"


def test_road_affected_length(flood):
    roads = gpd.GeoDataFrame({"highway": ["primary", "track"], "motorable": [True, True]},
                             geometry=[LineString([(-200, 50), (300, 50)]),
                                       LineString([(-200, 500), (300, 500)])], crs=EPSG)
    hit = affected_roads(roads, flood, 0)
    assert list(hit.index) == [0]
    assert hit.loc[0, "affected_m"] == pytest.approx(100.0)  # x = 0..100 inside
    assert hit.loc[0, "confidence"] == "high"


def test_bridge_evidence_deck_reach_or_none(flood):
    bridges = gpd.GeoDataFrame(
        {"highway": ["primary", "footway", "track"], "motorable": [True, False, True]},
        geometry=[LineString([(120, 50), (140, 50)]),    # 20 m from flood → deck/approach
                  LineString([(1200, 50), (1220, 50)]),  # 100 m from debris → river reach
                  LineString([(3000, 50), (3020, 50)])],  # far away → not affected
        crs=EPSG)
    out = assess_bridges(bridges, flood, approach_m=30, reach_m=150)
    assert out.loc[0, "evidence"] == "deck/approach"
    assert out.loc[1, "evidence"] == "river reach" and out.loc[1, "flood_class"] == DEBRIS
    assert 2 not in out.index


def test_points_and_counts(flood):
    pts = gpd.GeoDataFrame({"category": ["hospital", "health facility"]},
                           geometry=[Point(80, 80), Point(700, 700)], crs=EPSG)
    hit = affected_points(pts, flood, 20)
    assert hit["category"].tolist() == ["hospital"]
    assert count_by(hit, "confidence", ["high", "medium", "low"]) == {
        "high": 1, "medium": 0, "low": 0}


def test_empty_inputs_are_handled(flood):
    empty = gpd.GeoDataFrame({"highway": [], "motorable": []}, geometry=[], crs=EPSG)
    assert affected_roads(empty, flood, 10).empty
    assert assess_bridges(empty, flood, 30, 150).empty
    no_flood = flood.iloc[0:0]
    pts = gpd.GeoDataFrame(geometry=[Point(0, 0)], crs=EPSG)
    assert affected_points(pts, no_flood, 20).empty


def test_fast_road_cutting_equals_merged_zone_method():
    """road_pieces_in must give exactly what intersecting with one merged zone gives."""
    import numpy as np
    import shapely

    from floodmap.damage import road_pieces_in

    rng = np.random.default_rng(3)
    zones = np.array([shapely.Point(x, y).buffer(r) for x, y, r in
                      zip(rng.uniform(0, 2000, 60), rng.uniform(0, 2000, 60),
                          rng.uniform(20, 150, 60), strict=True)], dtype=object)
    lines = np.array([shapely.LineString(rng.uniform(0, 2000, (5, 2))) for _ in range(80)],
                     dtype=object)
    fast = road_pieces_in(lines, zones)
    slow = shapely.intersection(lines, shapely.union_all(zones))
    np.testing.assert_allclose(shapely.length(fast), shapely.length(slow), atol=1e-6)
