"""OSM layer handling: untrusted-text cleaning, road/POI classification, building counts."""

from __future__ import annotations

import geopandas as gpd
import numpy as np
from shapely.geometry import LineString, Point, Polygon, box, mapping

from floodmap.osm import (
    clean_text,
    features_to_gdf,
    nearest_place,
    prepare_roads,
    settlement_building_counts,
    split_pois,
)

EPSG = 32645
MOTORABLE = ("primary", "secondary", "track", "unclassified")


def _f(geom, **tags):
    return {"type": "Feature", "geometry": mapping(geom), "properties": {"@osmId": "x", **tags}}


def test_clean_text_removes_control_and_bidi_characters():
    assert clean_text("Dhunche‮\x00 Bazaar\n\nX", 80) == "Dhunche Bazaar X"
    assert clean_text("a" * 500, 80) == "a" * 80
    assert clean_text("   ", 80) is None and clean_text(None, 80) is None


def test_population_tag_is_never_kept_and_names_are_cleaned():
    gdf = features_to_gdf([_f(Point(85.3, 28.1), place="village", name="Gatlang\x07",
                              population="5000")], EPSG, 80)
    assert "population" not in gdf.columns
    assert gdf.loc[0, "name"] == "Gatlang"
    assert gdf.crs.to_epsg() == EPSG


def test_invalid_footprints_are_repaired():
    bowtie = Polygon([(85.30, 28.10), (85.301, 28.101), (85.301, 28.10), (85.30, 28.101)])
    gdf = features_to_gdf([_f(bowtie, building="yes")], EPSG, 80)
    assert gdf.geometry.is_valid.all()


def _roads():
    rows = [("primary", None, None, None), ("track", None, None, None),
            ("path", None, None, None), ("track", None, "no", None),
            ("secondary", "yes", None, None), ("footway", "yes", None, None),
            ("unclassified", "no", None, "private")]
    geoms = [LineString([(i * 100, 0), (i * 100, 100)]) for i in range(len(rows))]
    return gpd.GeoDataFrame({"highway": [r[0] for r in rows], "bridge": [r[1] for r in rows],
                             "motor_vehicle": [r[2] for r in rows],
                             "access": [r[3] for r in rows]}, geometry=geoms, crs=EPSG)


def test_motorable_and_bridge_flags():
    roads = prepare_roads(_roads(), MOTORABLE)
    assert roads["motorable"].tolist() == [True, True, False, False, True, False, False]
    assert roads["is_bridge"].tolist() == [False, False, False, False, True, True, False]
    assert roads["length_m"].eq(100).all()


def test_split_pois_categories():
    pois = gpd.GeoDataFrame(
        {"place": ["village", None, None, None, "hamlet"],
         "amenity": [None, "hospital", "clinic", None, None],
         "healthcare": [None, None, None, None, None],
         "name": ["Gatlang", "District Hospital", None, "Shop", None],
         "name_en": [None, None, None, None, None]},
        geometry=[Point(0, 0), box(10, 10, 20, 20), Point(30, 30), Point(40, 40),
                  Point(50, 50)], crs=EPSG)
    out = split_pois(pois)
    assert out["places"]["label"].tolist() == ["Gatlang", "unnamed hamlet"]
    assert out["health"]["category"].tolist() == ["hospital", "health facility"]
    assert out["health"].geometry.geom_type.eq("Point").all()  # polygon → point
    assert set(out) == {"places", "health"}  # only what the brief's task names


def test_building_counts_by_nearest_place():
    places = gpd.GeoDataFrame({"label": ["A", "B", "Outside"], "place": ["village"] * 3},
                              geometry=[Point(0, 0), Point(1500, 0), Point(9000, 0)], crs=EPSG)
    houses = [box(x, 0, x + 8, 8) for x in (10, 50, 400, 1400, 1450, 5000)]
    buildings = gpd.GeoDataFrame(geometry=houses, crs=EPSG)
    aoi = box(-100, -100, 6000, 100)
    counted, unassigned = settlement_building_counts(places, buildings, 1000, aoi)
    assert counted["building_count"].tolist() == [3, 2, None]  # outside the area: unknown
    assert unassigned == 1  # the house at 5 km is near no named place
    assert nearest_place(buildings, places, 1000) == [0, 0, 0, 1, 1, None]


def test_settlements_from_building_groups_p18():
    from floodmap.osm import building_groups, make_settlements

    # group 1: 6 houses at x≈0 (named place "Gatlang" nearby); group 2: 5 houses 2 km east
    # (only "near Gatlang"); group 3: 5 houses 10 km away (coordinates); 2 isolated houses
    xs = [0, 30, 60, 90, 120, 150] + [2000 + 30 * i for i in range(5)] + \
         [10_000 + 30 * i for i in range(5)] + [5000, 7000]
    buildings = gpd.GeoDataFrame(geometry=[box(x, 0, x + 8, 8) for x in xs], crs=EPSG)
    places = gpd.GeoDataFrame({"label": ["Gatlang", "unnamed hamlet", "Ghost village"],
                               "name": ["Gatlang", None, "Ghost village"],
                               "name_en": [None, None, None],
                               "place": ["village", "hamlet", "hamlet"]},
                              geometry=[Point(100, 200), Point(2100, 100), Point(20_000, 0)],
                              crs=EPSG)
    aoi = box(-1000, -1000, 25_000, 1000)
    sett, per_building = make_settlements(buildings, places, aoi, 75, 5, 3000, 1000, 3000)
    by_name = dict(zip(sett["name"], sett["building_count"], strict=True))
    assert by_name["Gatlang"] == 6
    assert by_name["near Gatlang"] == 5  # the unnamed hamlet does not give a name
    assert any(n.startswith("settlement at ") and c == 5 for n, c in by_name.items())
    assert by_name["Ghost village"] == 0  # named place without mapped buildings is kept
    assert (per_building == "").sum() == 2  # isolated houses
    assert sett["settlement_id"].is_unique

    # a 6 km chain of houses 50 m apart is split, never one giant settlement
    chain = np.column_stack([np.arange(0, 6000, 50), np.zeros(120)])
    labels = building_groups(chain, 75, 3000)
    assert len(np.unique(labels)) >= 3
