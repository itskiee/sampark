"""Scene search in the public Copernicus Data Space STAC catalogue (no login needed).

Used to find which Sentinel-1 acquisitions exist over an area around the flood date, with
the metadata needed to pair images correctly: relative orbit and pass direction (= the
"orbit track"), acquisition time, platform and polarisations.
All requests go through floodmap.net (allowlisted host, timeout, retries).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from floodmap.data_rules import CDSE_STAC_URL, check_stac_collection
from floodmap.net import request_json

MAX_PAGES = 20  # hard stop: a valid search for one area/period is far below this


@dataclass(frozen=True)
class S1Scene:
    scene_id: str
    acquired: datetime  # UTC start of acquisition
    platform: str  # e.g. "sentinel-1d"
    relative_orbit: int
    orbit_state: str  # "ascending" | "descending"
    mode: str  # "IW", "EW", ...
    polarizations: tuple[str, ...]
    footprint: BaseGeometry  # WGS84
    incidence_angle: float | None = None  # degrees from vertical at scene centre

    @property
    def track(self) -> tuple[int, str]:
        """Images are only comparable pixel-by-pixel within the same track."""
        return (self.relative_orbit, self.orbit_state)


def _parse_time(text: str) -> datetime:
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_scene(feature: dict[str, Any]) -> S1Scene | None:
    props = feature.get("properties") or {}
    try:
        return S1Scene(
            scene_id=str(feature["id"]),
            acquired=_parse_time(props.get("start_datetime") or props["datetime"]),
            platform=str(props.get("platform", "unknown")),
            relative_orbit=int(props["sat:relative_orbit"]),
            orbit_state=str(props["sat:orbit_state"]).lower(),
            mode=str(props.get("sar:instrument_mode", "")),
            polarizations=tuple(props.get("sar:polarizations") or ()),
            footprint=shape(feature["geometry"]),
            incidence_angle=_float_or_none(props.get("view:incidence_angle")),
        )
    except (KeyError, TypeError, ValueError):
        return None  # incomplete metadata: skip the scene rather than crash


@dataclass(frozen=True)
class S2Scene:
    scene_id: str
    acquired: datetime
    cloud_cover: float  # % of the tile (catalogue estimate)
    footprint: BaseGeometry


def search_s2_scenes(bbox, start: datetime, end: datetime, *, timeout_s: float,
                     retries: int) -> list[S2Scene]:
    """Sentinel-2 L2A scenes intersecting `bbox` between `start` and `end` (UTC)."""
    body: dict[str, Any] | None = {
        "collections": [check_stac_collection("sentinel-2-l2a")],
        "bbox": list(bbox),
        "datetime": f"{start.strftime('%Y-%m-%dT%H:%M:%SZ')}/{end.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "limit": 200,
    }
    url, method = f"{CDSE_STAC_URL}/search", "POST"
    scenes: list[S2Scene] = []
    for _ in range(MAX_PAGES):
        page = request_json(method, url, timeout_s=timeout_s, retries=retries, json_body=body)
        for f in page.get("features", []):
            p = f.get("properties") or {}
            try:
                scenes.append(S2Scene(str(f["id"]), _parse_time(p["datetime"]),
                                      float(p.get("eo:cloud_cover", 100.0)),
                                      shape(f["geometry"])))
            except (KeyError, TypeError, ValueError):
                continue
        nxt = next((ln for ln in page.get("links", []) if ln.get("rel") == "next"), None)
        if not nxt:
            break
        url = nxt["href"]
        method = str(nxt.get("method", "GET")).upper()
        body = nxt.get("body") if method == "POST" else None
    return sorted(scenes, key=lambda s: s.acquired)


def search_s1_scenes(
    bbox: tuple[float, float, float, float],
    start: datetime,
    end: datetime,
    *,
    timeout_s: float,
    retries: int,
) -> list[S1Scene]:
    """All Sentinel-1 GRD scenes intersecting `bbox` between `start` and `end` (UTC)."""
    body: dict[str, Any] | None = {
        "collections": [check_stac_collection("sentinel-1-grd")],
        "bbox": list(bbox),
        "datetime": f"{start.strftime('%Y-%m-%dT%H:%M:%SZ')}/{end.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "limit": 200,
    }
    url, method = f"{CDSE_STAC_URL}/search", "POST"
    scenes: list[S1Scene] = []
    for _ in range(MAX_PAGES):
        page = request_json(method, url, timeout_s=timeout_s, retries=retries, json_body=body)
        for feature in page.get("features", []):
            scene = _to_scene(feature)
            if scene is not None:
                scenes.append(scene)
        nxt = next((ln for ln in page.get("links", []) if ln.get("rel") == "next"), None)
        if not nxt:
            break
        url = nxt["href"]
        method = str(nxt.get("method", "GET")).upper()
        body = nxt.get("body") if method == "POST" else None
    return sorted(scenes, key=lambda s: s.acquired)
