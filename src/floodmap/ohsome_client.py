"""ohsome API client — the ONLY source of OpenStreetMap data in this project.

ohsome serves OSM history, so we can ask for OSM exactly as it was at a timestamp before
the event (see data_rules.ohsome_snapshot).

How the pre-event snapshot is obtained (and why this way):
- The brief names the v1 API (https://api.ohsome.org/v1). In August 2026 ohsome restricted
  the v1 snapshot extraction endpoints (`/elements/geometry`, `/elements/centroid` return
  HTTP 403; ohsome-api issue #346), while the v1 full-history endpoint keeps working.
- `/elementsFullHistory/geometry` returns every version of every OSM object in a time
  window, each with `@validFrom` / `@validTo`. We ask for a short window ENDING at the
  snapshot timestamp and keep only the versions still valid at that moment
  (`@validTo == snapshot`). That is, by definition, OSM as it was at the snapshot.
  Verified on 2026-10-12 against ohsome's own `/elements/count` at the same timestamp:
  identical counts for buildings, roads and places (see docs/explainers/M3_osm_damage.md).
- Runtime guard: a kept version that started after the snapshot or is valid beyond it
  raises DataRuleViolation (it could contain post-event edits).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any

from floodmap.data_rules import OHSOME_API_URL, DataRuleViolation
from floodmap.net import request_json

_TS = "%Y-%m-%dT%H:%M:%SZ"


def latest_data_timestamp(timeout_s: float = 30, retries: int = 1) -> datetime:
    """Newest OSM timestamp available in ohsome (GET /metadata)."""
    meta = request_json("GET", f"{OHSOME_API_URL}/metadata", timeout_s=timeout_s, retries=retries)
    raw = meta["extractRegion"]["temporalExtent"]["toTimestamp"]  # e.g. "2026-07-27T09:00Z"
    return _parse_ts(raw)


def _parse_ts(raw: str) -> datetime:
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def split_bbox(bbox: tuple[float, float, float, float], max_tile_km2: float
               ) -> list[tuple[float, float, float, float]]:
    """Split a lon/lat bbox into a regular grid of tiles of at most ~max_tile_km2 each.

    Large requests can hit ohsome's server timeout; several medium requests are more
    reliable and each one is retried on its own.
    """
    w, s, e, n = bbox
    km_x = (e - w) * 111.32 * math.cos(math.radians((s + n) / 2))
    km_y = (n - s) * 110.57
    tiles = max(1, math.ceil(km_x * km_y / max_tile_km2))
    nx = max(1, round(math.sqrt(tiles * km_x / max(km_y, 1e-9))))
    ny = max(1, math.ceil(tiles / nx))
    xs = [w + (e - w) * i / nx for i in range(nx + 1)]
    ys = [s + (n - s) * j / ny for j in range(ny + 1)]
    return [(xs[i], ys[j], xs[i + 1], ys[j + 1]) for i in range(nx) for j in range(ny)]


def keep_valid_at(features: list[dict[str, Any]], snapshot: datetime) -> list[dict[str, Any]]:
    """Versions of OSM objects valid at `snapshot` (from a window ending at `snapshot`).

    Raises DataRuleViolation if any version is dated after the snapshot: that would mean
    the request window was wrong and post-snapshot (possibly post-event) edits leaked in.
    """
    kept = []
    for feat in features:
        props = feat.get("properties") or {}
        valid_from = _parse_ts(props["@validFrom"])
        valid_to = _parse_ts(props["@validTo"])
        if valid_from > snapshot or valid_to > snapshot:
            raise DataRuleViolation(
                f"OSM version {props.get('@osmId')} is dated after the snapshot {snapshot:{_TS}}"
                f" (valid {valid_from:{_TS}} – {valid_to:{_TS}}): refusing post-snapshot data")
        if valid_to == snapshot:  # still valid at the end of the window = at the snapshot
            kept.append(feat)
    return kept


def fetch_snapshot(osm_filter: str, bbox: tuple[float, float, float, float],
                   snapshot: datetime, *, timeout_s: float, retries: int,
                   window_hours: float, max_tile_km2: float) -> list[dict[str, Any]]:
    """GeoJSON features matching `osm_filter` in `bbox`, as OSM was at `snapshot`.

    Geometries are not clipped to the tiles (clipGeometry=false), so an object crossing a
    tile border arrives whole from both tiles and is de-duplicated by its OSM id.
    """
    if snapshot.tzinfo is None:
        snapshot = snapshot.replace(tzinfo=UTC)
    start = snapshot - timedelta(hours=window_hours)
    by_id: dict[str, dict[str, Any]] = {}
    for tile in split_bbox(bbox, max_tile_km2):
        response = request_json(
            "POST", f"{OHSOME_API_URL}/elementsFullHistory/geometry",
            data={"bboxes": ",".join(f"{v:.6f}" for v in tile),
                  "time": f"{start:{_TS}},{snapshot:{_TS}}",
                  "filter": osm_filter, "properties": "tags", "clipGeometry": "false"},
            timeout_s=timeout_s, retries=retries)
        for feat in keep_valid_at(response.get("features", []), snapshot):
            by_id.setdefault(feat["properties"]["@osmId"], feat)
    return list(by_id.values())
