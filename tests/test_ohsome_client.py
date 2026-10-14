"""ohsome client: pre-event snapshot reconstruction from the full-history endpoint."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

import sampark.ohsome_client as oc
from sampark.data_rules import DataRuleViolation

SNAP = datetime(2026, 7, 27, 9, 0, tzinfo=UTC)


def _feat(osm_id, valid_from, valid_to, lon=85.3, lat=28.1, **tags):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"@osmId": osm_id, "@validFrom": valid_from, "@validTo": valid_to,
                           **tags}}


def test_keep_valid_at_keeps_only_versions_valid_at_snapshot():
    feats = [_feat("node/1", "2026-07-26T09:00:00Z", "2026-07-27T09:00:00Z"),  # valid
             _feat("node/2", "2026-07-26T09:00:00Z", "2026-07-26T20:00:00Z"),  # deleted before
             _feat("node/3", "2026-07-26T20:00:00Z", "2026-07-27T09:00:00Z")]  # new version
    kept = oc.keep_valid_at(feats, SNAP)
    assert [f["properties"]["@osmId"] for f in kept] == ["node/1", "node/3"]


@pytest.mark.parametrize("valid_from,valid_to", [
    ("2026-07-26T09:00:00Z", "2026-07-28T00:00:00Z"),  # valid beyond the snapshot
    ("2026-07-27T10:00:00Z", "2026-07-27T11:00:00Z"),  # created after the snapshot
])
def test_post_snapshot_versions_raise(valid_from, valid_to):
    with pytest.raises(DataRuleViolation, match="after the snapshot"):
        oc.keep_valid_at([_feat("way/9", valid_from, valid_to)], SNAP)


def test_split_bbox_covers_area_in_small_tiles():
    bbox = (85.10, 27.85, 85.45, 28.32)  # ~1,800 km²
    tiles = oc.split_bbox(bbox, 400)
    assert len(tiles) >= 5
    assert min(t[0] for t in tiles) == bbox[0] and max(t[2] for t in tiles) == bbox[2]
    assert min(t[1] for t in tiles) == bbox[1] and max(t[3] for t in tiles) == bbox[3]
    assert oc.split_bbox((85.30, 28.15, 85.32, 28.17), 400) == [(85.30, 28.15, 85.32, 28.17)]


def test_fetch_snapshot_request_and_dedup(monkeypatch):
    calls = []

    def fake_request(method, url, *, data, timeout_s, retries):
        calls.append((method, url, data))
        # the same way arrives from both tiles (geometries are not clipped)
        return {"features": [
            _feat("way/1", "2026-07-26T09:00:00Z", "2026-07-27T09:00:00Z", highway="track"),
            _feat(f"way/{len(calls) + 10}", "2026-07-26T09:00:00Z", "2026-07-27T09:00:00Z"),
            _feat("way/5", "2026-07-26T09:00:00Z", "2026-07-27T01:00:00Z")]}  # old version

    monkeypatch.setattr(oc, "request_json", fake_request)
    feats = oc.fetch_snapshot("highway=*", (85.0, 28.0, 85.6, 28.4), SNAP, timeout_s=5,
                              retries=0, window_hours=24, max_tile_km2=1000)
    assert len(calls) >= 2
    method, url, data = calls[0]
    assert method == "POST" and url.endswith("/v1/elementsFullHistory/geometry")
    assert url.startswith("https://api.ohsome.org/")
    assert data["time"] == "2026-07-26T09:00:00Z,2026-07-27T09:00:00Z"
    assert data["clipGeometry"] == "false" and data["filter"] == "highway=*"
    ids = [f["properties"]["@osmId"] for f in feats]
    assert ids.count("way/1") == 1 and "way/5" not in ids
    assert len(ids) == len(calls) + 1
