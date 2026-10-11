"""Data-rules enforcement tests (breaking the rules = disqualification).

1. The real pipeline code (src/, app/, scripts/) contains no violations.
2. The scanner itself catches every kind of planted violation (so a broken scanner
   cannot silently pass).
3. Runtime guards reject non-allowed data collections and HTTP hosts.
4. The OSM snapshot is always strictly before the event, for any event date.
5. Secret files and data folders are really ignored by git.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, date, datetime

import pytest

from floodmap.config import REPO_ROOT
from floodmap.data_rules import (
    ALLOWED_OPENEO_COLLECTIONS,
    ALLOWED_STAC_COLLECTIONS,
    DataRuleViolation,
    check_http_url,
    check_openeo_collection,
    check_stac_collection,
    ohsome_snapshot,
)
from tests.rules_scanner import scan_repo, scan_source, scanned_file_count


# ---------------------------------------------------------------------------
# 1. Real code
# ---------------------------------------------------------------------------
def test_pipeline_code_has_no_data_rule_violations():
    assert scanned_file_count(REPO_ROOT) > 0, "scanner found no files: wrong repo root?"
    violations = scan_repo(REPO_ROOT)
    assert not violations, "Data-rule violations:\n" + "\n".join(map(str, violations))


# ---------------------------------------------------------------------------
# 2. Scanner self-tests: each snippet must trigger the named rule
# ---------------------------------------------------------------------------
PLANTED = [
    ("import validation.emsr927", "validation-isolation"),
    ("from validation import compare", "validation-isolation"),
    ("p = 'validation/data/flood.shp'", "validation-isolation"),
    ("p = r'validation\\data\\x.gpkg'", "validation-isolation"),
    ("from pathlib import Path\np = Path('.') / 'validation'", "validation-isolation"),
    ("import osmnx as ox", "osmnx"),
    ("from osmnx import graph_from_place", "osmnx"),
    ("g = ox.graph_from_bbox(1, 2, 3, 4)", "osmnx"),
    ("f = ox.features_from_polygon(poly, tags)", "osmnx"),
    ("URL = 'https://overpass-api.de/api/interpreter'", "overpass"),
    ("def query_overpass(q): pass", "overpass"),
    ("w = fetch('JRC/GSW1_4/GlobalSurfaceWater')", "forbidden-source"),
    ("lc = fetch('ESA/WorldCover/v200')", "forbidden-source"),
    ("pop = fetch('WorldPop/GP/100m/pop')", "forbidden-source"),
    ("d = fetch('USGS/SRTMGL1_003')", "forbidden-source"),
    ("h = fetch('MERIT/Hydro/v1_0_1')", "forbidden-source"),
    ("c = fetch('GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED')", "forbidden-source"),
    ("ref = load('EMSR927_AOI01_DEL_PRODUCT.shp')", "forbidden-source"),
    ("import ee", "earth-engine"),
    ("import geemap", "earth-engine"),
    ("from ee import ImageCollection", "earth-engine"),
    ("u = 'https://openeofed.dataspace.copernicus.eu'", "earth-engine"),
    ("cube = conn.load_collection('LANDSAT8_L2')", "collection"),
    ("cube = conn.load_collection(collection_id='SENTINEL3_OLCI_L1B')", "collection"),
    ("cube = load_collection(conn, 'ESA_WORLDCOVER_10M_2021_V2')", "forbidden-source"),
    ("import requests", "http-library"),
    ("from urllib.request import urlopen", "http-library"),
    ("import httpx", "http-library"),
    ("url = 'https://example.com/data.zip'", "url"),
    ("url = f'https://evil.example/{x}'", "url"),
]


@pytest.mark.parametrize(("snippet", "rule"), PLANTED)
def test_scanner_catches_planted_violation(snippet, rule):
    rules = {v.rule for v in scan_source(snippet, "src/floodmap/planted.py")}
    assert rule in rules, f"scanner missed rule '{rule}' in: {snippet!r} (found {rules})"


def test_scanner_accepts_clean_code():
    clean = (
        "from urllib.parse import urlsplit\n"
        "from floodmap.cdse_client import load_collection\n"
        "s1 = conn.load_collection('SENTINEL1_GRD')\n"
        "dem = load_collection(conn, 'COPERNICUS_30')\n"
        "s2 = conn.load_collection(collection_id='SENTINEL2_L2A')\n"
        "API = 'https://api.ohsome.org/v1'\n"
        "STAC = 'https://stac.dataspace.copernicus.eu/v1'\n"
        "msg = 'input validation failed'\n"
    )
    assert scan_source(clean, "src/floodmap/clean.py") == []


def test_http_library_allowed_only_in_net_module():
    assert scan_source("import requests", "src/floodmap/net.py") == []


# ---------------------------------------------------------------------------
# 3. Runtime guards
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("collection", sorted(ALLOWED_OPENEO_COLLECTIONS))
def test_allowed_openeo_collections_pass(collection):
    assert check_openeo_collection(collection) == collection


@pytest.mark.parametrize(
    "collection",
    ["LANDSAT8_L2", "ESA_WORLDCOVER_10M_2021_V2", "SENTINEL1_GLOBAL_MOSAICS",
     "COPERNICUS_VEGETATION_PHENOLOGY_PRODUCTIVITY_10M_SEASON1", "sentinel1_grd", ""],
)
def test_forbidden_openeo_collections_raise(collection):
    with pytest.raises(DataRuleViolation):
        check_openeo_collection(collection)


@pytest.mark.parametrize("collection", sorted(ALLOWED_STAC_COLLECTIONS))
def test_allowed_stac_collections_pass(collection):
    assert check_stac_collection(collection) == collection


def test_forbidden_stac_collection_raises():
    with pytest.raises(DataRuleViolation):
        check_stac_collection("cop-dem-glo-90")


@pytest.mark.parametrize(
    "url",
    ["https://api.ohsome.org/v1/elements/geometry", "https://stac.dataspace.copernicus.eu/v1/search"],
)
def test_allowed_http_urls_pass(url):
    assert check_http_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "http://api.ohsome.org/v1/metadata",  # not HTTPS
        "https://overpass-api.de/api/interpreter",
        "https://api.ohsome.org.evil.example/v1",  # look-alike host
        "https://mapping.emergency.copernicus.eu/activations/EMSR927",
        "https://earthengine.googleapis.com/v1/projects",
        "https://openeofed.dataspace.copernicus.eu/openeo/1.2",
        "ftp://api.ohsome.org/",
    ],
)
def test_forbidden_http_urls_raise(url):
    with pytest.raises(DataRuleViolation):
        check_http_url(url)


# ---------------------------------------------------------------------------
# 4. OSM snapshot rule
# ---------------------------------------------------------------------------
def test_snapshot_trishuli_uses_latest_available_data():
    snap = ohsome_snapshot(date(2026, 8, 26))
    assert snap == datetime(2026, 7, 27, 9, 0, tzinfo=UTC)


def test_snapshot_older_event_uses_day_before():
    assert ohsome_snapshot(date(2021, 6, 15)) == datetime(2021, 6, 14, tzinfo=UTC)


def test_snapshot_prefers_live_metadata_when_given():
    live = datetime(2026, 9, 30, tzinfo=UTC)  # if ohsome had newer data...
    assert ohsome_snapshot(date(2026, 8, 26), live) == datetime(2026, 8, 25, tzinfo=UTC)


@pytest.mark.parametrize("day", [date(2015, 1, 1), date(2023, 10, 4), date(2026, 7, 27),
                                 date(2026, 7, 28), date(2026, 8, 26), date(2030, 1, 1)])
def test_snapshot_always_strictly_before_event(day):
    snap = ohsome_snapshot(day)
    assert snap < datetime(day.year, day.month, day.day, tzinfo=UTC)


# ---------------------------------------------------------------------------
# 5. Git ignores secrets and data
# ---------------------------------------------------------------------------
GIT = shutil.which("git")


def _ignored(rel_path: str) -> bool:
    result = subprocess.run(
        [GIT, "check-ignore", "--no-index", "-q", rel_path], cwd=REPO_ROOT, check=False
    )
    return result.returncode == 0


@pytest.mark.skipif(GIT is None or not (REPO_ROOT / ".git").exists(), reason="needs git repo")
@pytest.mark.parametrize(
    "path",
    [".env", ".env.local", "cdse-service-account.json", "my_service_account.json",
     "keys/private.pem", "credentials.json", "cache/x/s1.tif", "outputs/run/results.json",
     "logs/floodmap.log", "validation/data/emsr927.zip", "docs/Track B.pdf"],
)
def test_sensitive_paths_are_git_ignored(path):
    assert _ignored(path), f"{path} is NOT git-ignored"


@pytest.mark.skipif(GIT is None or not (REPO_ROOT / ".git").exists(), reason="needs git repo")
@pytest.mark.parametrize("path", [".env.example", "src/floodmap/aoi.py", "configs/default.yaml"])
def test_project_files_are_not_git_ignored(path):
    assert not _ignored(path), f"{path} should be tracked but is git-ignored"
