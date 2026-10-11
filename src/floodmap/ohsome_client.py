"""ohsome API client — the ONLY source of OpenStreetMap data in this project.

ohsome serves OSM history, so we can ask for OSM exactly as it was at a timestamp before
the event (see data_rules.ohsome_snapshot). Milestone M0 only needs the metadata call;
feature downloads (buildings, roads, bridges, places, health facilities) arrive in M3.
"""

from __future__ import annotations

from datetime import UTC, datetime

from floodmap.data_rules import OHSOME_API_URL
from floodmap.net import request_json


def latest_data_timestamp(timeout_s: float = 30, retries: int = 1) -> datetime:
    """Newest OSM timestamp available in ohsome (GET /metadata)."""
    meta = request_json("GET", f"{OHSOME_API_URL}/metadata", timeout_s=timeout_s, retries=retries)
    raw = meta["extractRegion"]["temporalExtent"]["toTimestamp"]  # e.g. "2026-07-27T09:00Z"
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
