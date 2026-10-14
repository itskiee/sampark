from __future__ import annotations

import json

import pytest
from shapely.geometry import Polygon

from sampark.aoi import AOIError, aoi_hash, parse_aoi, utm_epsg_for

LIMITS = {"max_area_km2": 2500, "min_area_km2": 0.5}
TRISHULI = [85.10, 27.85, 85.45, 28.32]


def test_bbox_area_and_projection():
    aoi = parse_aoi(TRISHULI, **LIMITS)
    # 0.35° lon x 0.47° lat at ~28°N is roughly 34 km x 52 km
    assert 1700 < aoi.area_km2 < 1900
    assert aoi.utm_epsg == 32645  # UTM zone 45N
    assert aoi.bounds == tuple(TRISHULI)


@pytest.mark.parametrize(
    ("bbox", "message"),
    [
        ([85.45, 27.85, 85.10, 28.32], "min < max"),
        ([85.1, 28.0, 85.2], "4 numbers"),
        (["a", 1, 2, 3], "4 numbers"),
        ([185, 27, 186, 28], "longitudes"),
        ([85, -95, 86, 28], "latitudes"),
        ([80.0, 25.0, 84.0, 29.0], "larger than"),
        ([85.0, 28.0, 85.001, 28.001], "smaller than"),
    ],
)
def test_bad_bboxes_give_clear_errors(bbox, message):
    with pytest.raises(AOIError, match=message):
        parse_aoi(bbox, **LIMITS)


def test_geojson_feature_and_collection():
    poly = {"type": "Polygon", "coordinates": [[[85.3, 28.1], [85.4, 28.1], [85.4, 28.2],
                                                [85.3, 28.2], [85.3, 28.1]]]}
    feature = {"type": "Feature", "properties": {}, "geometry": poly}
    fc = {"type": "FeatureCollection", "features": [feature]}
    a, b, c = (parse_aoi(x, **LIMITS) for x in (poly, feature, fc))
    assert a.aoi_hash == b.aoi_hash == c.aoi_hash


def test_point_geometry_rejected():
    with pytest.raises(AOIError, match="Polygon"):
        parse_aoi({"type": "Point", "coordinates": [85.3, 28.1]}, **LIMITS)


def test_empty_feature_collection_rejected():
    with pytest.raises(AOIError, match="empty"):
        parse_aoi({"type": "FeatureCollection", "features": []}, **LIMITS)


def test_self_intersecting_polygon_is_repaired():
    bowtie = Polygon([(85.3, 28.1), (85.4, 28.2), (85.4, 28.1), (85.3, 28.2), (85.3, 28.1)])
    assert not bowtie.is_valid
    aoi = parse_aoi(bowtie, **LIMITS)
    assert aoi.geometry.is_valid and aoi.area_km2 > 0.5


def test_geojson_file_and_bad_files(tmp_path):
    good = tmp_path / "aoi.geojson"
    good.write_text(json.dumps({"type": "Polygon", "coordinates": [[[85.3, 28.1], [85.4, 28.1],
                    [85.4, 28.2], [85.3, 28.1]]]}), encoding="utf-8")
    assert parse_aoi(good, **LIMITS).area_km2 > 0.5

    bad_json = tmp_path / "broken.geojson"
    bad_json.write_text("{not json", encoding="utf-8")
    with pytest.raises(AOIError, match="not valid JSON"):
        parse_aoi(bad_json, **LIMITS)

    with pytest.raises(AOIError, match="existing .geojson"):
        parse_aoi(tmp_path / "missing.geojson", **LIMITS)
    with pytest.raises(AOIError, match="existing .geojson"):
        parse_aoi(tmp_path / "notes.txt", **LIMITS)


def test_hash_ignores_vertex_order():
    a = Polygon([(85.3, 28.1), (85.4, 28.1), (85.4, 28.2), (85.3, 28.2)])
    b = Polygon([(85.4, 28.2), (85.3, 28.2), (85.3, 28.1), (85.4, 28.1)])  # other start
    c = Polygon(list(reversed(a.exterior.coords)))  # other orientation
    assert aoi_hash(a) == aoi_hash(b) == aoi_hash(c)
    assert aoi_hash(a) != aoi_hash(Polygon([(85.3, 28.1), (85.5, 28.1), (85.5, 28.2)]))


@pytest.mark.parametrize(
    ("lon", "lat", "epsg"),
    [(85.3, 28.1, 32645), (-0.1, 51.5, 32630), (151.2, -33.9, 32756), (179.9, 10, 32660),
     (-180, 0, 32601)],
)
def test_utm_zone(lon, lat, epsg):
    assert utm_epsg_for(lon, lat) == epsg


def test_unsupported_type():
    with pytest.raises(AOIError, match="Unsupported"):
        parse_aoi(42, **LIMITS)
