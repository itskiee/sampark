"""End-to-end pipeline test without network or credentials.

A synthetic world: a straight N-S valley in the middle of the area. Two Sentinel-1 tracks
(ascending + descending) see the valley floor turn into water after the event; the DEM is
a V-shaped valley. CDSE calls (catalogue, openEO) are replaced by local fakes that write
GeoTIFFs in exactly the format the real ones return, so every stage, the cache, the
results.json and the PNGs run for real. This lets CI test the whole pipeline.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pytest
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin
from shapely.geometry import LineString, Point, box, mapping

from floodmap.catalogue import S1Scene
from floodmap.config import load_config
from floodmap.pipeline import run_pipeline
from floodmap.results import StageStatus
from floodmap.s1_pairing import to_utm

AOI_BBOX = [85.30, 28.12, 85.40, 28.22]
EVENT = date(2026, 8, 26)
RES = 100.0  # coarse pixels keep the test fast


def _grid(bounds_m, res):
    minx, miny, maxx, maxy = bounds_m
    minx, maxy = np.floor(minx / res) * res, np.ceil(maxy / res) * res
    width = int(np.ceil((maxx - minx) / res))
    height = int(np.ceil((maxy - miny) / res))
    return from_origin(minx, maxy, res, res), (height, width)


def _valley_x(aoi):
    minx, _, maxx, _ = to_utm(aoi.geometry, aoi.utm_epsg).bounds
    return (minx + maxx) / 2


def _write(path, layers: dict, transform, epsg):
    first = next(iter(layers.values()))
    with rasterio.open(path, "w", driver="GTiff", height=first.shape[0], width=first.shape[1],
                       count=len(layers), dtype="float32", crs=f"EPSG:{epsg}",
                       transform=transform) as dst:
        for i, (name, arr) in enumerate(layers.items(), start=1):
            dst.write(arr.astype("float32"), i)
            dst.set_band_description(i, name)


def fake_scenes(bbox, start, end, *, timeout_s, retries):
    foot = box(84.9, 27.8, 85.8, 28.6)
    scenes = []
    for orbit, state, first, hhmm in ((85, "ascending", "2026-07-23", "12:21"),
                                      (19, "descending", "2026-07-31", "00:18")):
        d0 = date.fromisoformat(first)
        for i in range(6):
            day = d0 + timedelta(days=12 * i)
            when = datetime.fromisoformat(f"{day}T{hhmm}:00+00:00").astimezone(UTC)
            scenes.append(S1Scene(f"{orbit}-{day}", when, "sentinel-1d", orbit, state, "IW",
                                  ("VV", "VH"), foot, 39.0))
    return scenes


def fake_s1_composite(conn, aoi, pair, bands, resolution, out_path):
    transform, shape = _grid(to_utm(aoi.geometry, aoi.utm_epsg).bounds, resolution)
    xs = transform.c + (np.arange(shape[1]) + 0.5) * resolution
    in_valley = np.abs(xs - _valley_x(aoi)) < 250  # 500 m wide valley floor
    db = lambda v: np.full(shape, 10 ** (v / 10))  # noqa: E731
    pre_vv, pre_vh, post_vv, post_vh = db(-8), db(-15), db(-8), db(-15)
    post_vv[:, in_valley] = 10 ** (-21 / 10)  # valley floor became open water
    post_vh[:, in_valley] = 10 ** (-27 / 10)
    _write(out_path, {"pre_VV": pre_vv, "pre_VH": pre_vh, "post_VV": post_vv,
                      "post_VH": post_vh}, transform, aoi.utm_epsg)
    return out_path


def fake_dem(conn, aoi, buffer_m, resolution, out_path):
    minx, miny, maxx, maxy = to_utm(aoi.geometry, aoi.utm_epsg).bounds
    transform, shape = _grid((minx - buffer_m, miny - buffer_m, maxx + buffer_m,
                              maxy + buffer_m), resolution)
    xs = transform.c + (np.arange(shape[1]) + 0.5) * resolution
    rows = np.arange(shape[0])[:, None]
    dem = 600 + np.abs(xs - _valley_x(aoi))[None, :] * 0.4 + (shape[0] - rows) * 2.0
    _write(out_path, {"DEM": dem}, transform, aoi.utm_epsg)
    return out_path


def fake_osm_world(aoi):
    """Synthetic pre-event OSM: a village + road + bridge on the valley floor, a village and
    a hospital on the slope 2 km east (safe), as ohsome full-history features."""
    epsg = aoi.utm_epsg
    vx = _valley_x(aoi)
    _, miny, _, maxy = to_utm(aoi.geometry, epsg).bounds
    cy = (miny + maxy) / 2
    back = Transformer.from_crs(epsg, 4326, always_xy=True)

    def feat(geom, osm_id, **tags):
        from shapely import transform
        g = transform(geom, lambda xy: np.column_stack(back.transform(xy[:, 0], xy[:, 1])))
        return {"type": "Feature", "geometry": mapping(g),
                "properties": {"@osmId": osm_id, "@validFrom": "2026-07-26T09:00:00Z",
                               "@validTo": "2026-07-27T09:00:00Z", **tags}}

    houses = [feat(Point(vx + dx, cy + dy).buffer(5, cap_style="square"), f"way/{i}",
                   building="yes")
              for i, (dx, dy) in enumerate([(0, 0), (50, 80), (-60, -40), (2000, 0),
                                            (2050, 60)])]
    roads = [feat(LineString([(vx, miny + 200), (vx, maxy - 200)]), "way/100",
                  highway="secondary", name="Valley Road"),
             feat(LineString([(vx - 150, cy + 1500), (vx + 150, cy + 1500)]), "way/101",
                  highway="tertiary", bridge="yes", name="Valley Bridge"),
             feat(LineString([(vx, cy - 3000), (vx + 2000, cy - 3000), (vx + 2000, cy)]),
                  "way/102", highway="track")]
    pois = [feat(Point(vx, cy), "node/1", place="village", name="Floorvillage"),
            feat(Point(vx + 2000, cy), "node/2", place="village", name="Slopevillage"),
            feat(Point(vx + 2100, cy + 100), "node/3", amenity="hospital",
                 name="Hill Hospital")]

    def fetch(osm_filter, bbox, snapshot, **kw):
        if osm_filter.startswith("building"):
            return houses
        return roads if osm_filter.startswith("highway") else pois

    return fetch


@pytest.fixture
def offline_world(monkeypatch):
    import floodmap.stages.s1 as s1_stage
    import floodmap.stages.s2 as s2_stage
    import floodmap.stages.terrain as terrain_stage

    monkeypatch.setattr(s1_stage, "search_s1_scenes", fake_scenes)
    monkeypatch.setattr(s1_stage, "connect", lambda: object())
    monkeypatch.setattr(s1_stage, "fetch_pair_composite", fake_s1_composite)
    monkeypatch.setattr(terrain_stage, "connect", lambda: object())
    monkeypatch.setattr(terrain_stage, "fetch_dem", fake_dem)
    monkeypatch.setattr(s2_stage, "search_s2_scenes",
                        lambda *a, **k: [])  # monsoon: no clear optical image
    import floodmap.stages.osm as osm_stage
    from floodmap.aoi import parse_aoi
    from floodmap.data_rules import OHSOME_LATEST_KNOWN

    monkeypatch.setattr(osm_stage, "latest_data_timestamp", lambda **k: OHSOME_LATEST_KNOWN)
    monkeypatch.setattr(osm_stage, "fetch_snapshot",
                        fake_osm_world(parse_aoi(AOI_BBOX, 2500, 0.01)))


def test_full_pipeline_offline(tmp_path, offline_world):
    config = load_config(overrides={
        "cache": {"dir": str(tmp_path / "cache")},
        "output": {"dir": str(tmp_path / "outputs")},
        "logging": {"dir": str(tmp_path / "logs")},
        "s1": {"resolution_m": RES, "min_mapping_unit_m2": RES**2},
        "terrain": {"dem_resolution_m": RES, "buffer_m": 2000, "stream_area_km2": 1.0},
        "confidence": {"medium_min_patch_m2": 5 * RES**2},
    })
    start = (85.35, 28.215)
    results = run_pipeline(AOI_BBOX, EVENT, config, event_name="synthetic valley",
                           stages=["s1", "s2", "terrain", "confidence", "osm", "flood_path",
                                   "damage"],
                           focus_bbox=(85.33, 28.14, 85.37, 28.20), flood_path_start=start)
    st = results.stages
    assert st["s1"].status == StageStatus.OK, st["s1"].message
    assert len(st["s1"].outputs["tracks"]) == 2  # ascending + descending
    assert st["s2"].status == StageStatus.SKIPPED and "monsoon" in st["s2"].message
    assert st["terrain"].status == StageStatus.OK, st["terrain"].message
    assert st["confidence"].status == StageStatus.OK, st["confidence"].message
    conf = st["confidence"].outputs
    assert conf["by_class_km2"]["new water-like"] > 0.5
    assert conf["affected_by_confidence_km2"]["high"] > 0.5  # both tracks agree
    assert st["flood_path"].status == StageStatus.OK, st["flood_path"].message
    assert st["flood_path"].outputs["share_crossing_detected_change"] > 0.5

    # pre-event OSM + damage overlay
    assert st["osm"].status == StageStatus.OK, (st["osm"].message, st["osm"].warnings)
    assert st["osm"].outputs["buildings"] == 5 and st["osm"].outputs["hospitals"] == 1
    assert st["damage"].status == StageStatus.OK, (st["damage"].message, st["damage"].warnings)
    dmg = st["damage"].outputs
    assert dmg["buildings"]["potentially_affected"] == 3  # the 3 houses on the valley floor
    assert dmg["buildings"]["by_confidence"]["high"] == 3  # both tracks agree
    assert dmg["bridges"]["list"][0]["name"] == "Valley Bridge"
    assert dmg["roads"]["affected_km_motorable"] > 5  # the valley road
    assert dmg["health_facilities"] == []  # hospital on the slope is safe
    top = dmg["settlements"]["most_affected"]
    assert [s["name"] for s in top] == ["Floorvillage"] and top[0]["buildings"] == 3
    for folder in ("osm", "damage"):  # brief: published OSM-derived data stays ODbL
        assert "ODbL" in (results.run_dir / folder / "LICENSE_OSM_DATA.txt").read_text("utf-8")
    saved = json.loads((results.run_dir / "results.json").read_text(encoding="utf-8"))
    assert "ODbL" in saved["data_licenses"]["openstreetmap"]
    along = st["flood_path"].outputs["settlements_along_path"]
    assert [s["name"] for s in along] == ["Floorvillage"]  # slope village is 2 km away
    assert abs(along[0]["height_above_river_m"]) < 20

    data = json.loads((results.run_dir / "results.json").read_text(encoding="utf-8"))
    assert data["stages"]["confidence"]["outputs"]["files"]["polygons"].endswith(".geojson")
    for png in ("s1/s1_overview.png", "confidence/flood_map.png", "terrain/terrain.png",
                "flood_path/flood_path.png", "damage/damage_map.png"):
        assert (results.run_dir / png).stat().st_size > 10_000

    # second run is served from the cache and gives identical numbers
    again = run_pipeline(AOI_BBOX, EVENT, config, event_name="synthetic valley",
                         stages=["s1", "terrain", "confidence"])
    assert again.stages["confidence"].outputs["affected_total_km2"] == conf["affected_total_km2"]
    assert again.stages["s1"].outputs["tracks"][0]["cached_download"] is True


def test_area_without_osm_data_gives_clear_messages(tmp_path, offline_world, monkeypatch):
    import floodmap.stages.osm as osm_stage

    monkeypatch.setattr(osm_stage, "fetch_snapshot", lambda *a, **k: [])
    config = load_config(overrides={
        "cache": {"dir": str(tmp_path / "cache")},
        "output": {"dir": str(tmp_path / "outputs")},
        "logging": {"dir": str(tmp_path / "logs")},
        "s1": {"resolution_m": RES, "min_mapping_unit_m2": RES**2},
        "terrain": {"dem_resolution_m": RES, "buffer_m": 2000, "stream_area_km2": 1.0},
    })
    results = run_pipeline(AOI_BBOX, EVENT, config, event_name="unmapped valley",
                           stages=["damage", "flood_path"], flood_path_start=(85.35, 28.215))
    st = results.stages
    assert st["osm"].status == StageStatus.PARTIAL
    assert any("no buildings mapped" in w for w in st["osm"].warnings)
    assert any("no hospital" in w for w in st["osm"].warnings)
    assert st["damage"].usable, st["damage"].message
    assert st["damage"].outputs["buildings"]["potentially_affected"] == 0
    assert st["flood_path"].outputs["settlements_along_path"] == []
