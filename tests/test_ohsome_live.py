"""Live check (pytest -m network): our full-history reconstruction of the pre-event OSM
snapshot equals ohsome's own snapshot count at the same timestamp."""

from __future__ import annotations

import pytest

from sampark.data_rules import OHSOME_API_URL, OHSOME_LATEST_KNOWN
from sampark.net import request_json
from sampark.ohsome_client import fetch_snapshot

pytestmark = pytest.mark.network

BBOX = (85.30, 28.10, 85.36, 28.20)  # Syabrubesi area


@pytest.mark.parametrize("osm_filter", [
    "building=* and building!=no and geometry:polygon",
    "highway=* and type:way",
])
def test_reconstruction_matches_official_snapshot_count(osm_filter):
    ts = OHSOME_LATEST_KNOWN.strftime("%Y-%m-%dT%H:%M:%SZ")
    official = request_json("POST", f"{OHSOME_API_URL}/elements/count",
                            data={"bboxes": ",".join(map(str, BBOX)), "time": ts,
                                  "filter": osm_filter}, timeout_s=300, retries=2)
    expected = int(official["result"][0]["value"])
    feats = fetch_snapshot(osm_filter, BBOX, OHSOME_LATEST_KNOWN, timeout_s=300, retries=2,
                           window_hours=24, max_tile_km2=10_000)  # one tile: same clipping
    assert expected > 0
    assert len(feats) == expected
