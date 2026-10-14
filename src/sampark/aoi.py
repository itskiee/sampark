"""Area of interest (AOI): parsing, validation, projection choice and hashing.

Judges type or draw arbitrary areas, so this is the first line of defence against
crashes: every malformed input becomes an `AOIError` with a message a user can act on.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import shapely
from pyproj import Geod
from shapely.geometry import MultiPolygon, Polygon, box, mapping, shape

_GEOD = Geod(ellps="WGS84")


class AOIError(ValueError):
    """The area of interest is invalid; the message explains how to fix it."""


@dataclass(frozen=True)
class AOI:
    geometry: Polygon | MultiPolygon  # WGS84 lon/lat
    area_km2: float
    utm_epsg: int
    aoi_hash: str

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return tuple(round(v, 6) for v in self.geometry.bounds)  # type: ignore[return-value]

    def to_geojson(self) -> dict[str, Any]:
        return mapping(self.geometry)

    def summary(self) -> dict[str, Any]:
        return {
            "bbox": list(self.bounds),
            "area_km2": round(self.area_km2, 2),
            "utm_epsg": self.utm_epsg,
            "aoi_hash": self.aoi_hash,
        }


def utm_epsg_for(lon: float, lat: float) -> int:
    """EPSG code of the UTM zone containing (lon, lat).

    We process rasters in UTM so pixels are square and measured in metres; areas,
    distances and road lengths are then simple arithmetic. (The Norway/Svalbard zone
    exceptions are ignored: they only change which zone is used, not correctness.)
    """
    zone = min(int((lon + 180.0) // 6.0) + 1, 60)
    return (32600 if lat >= 0 else 32700) + zone


def geodesic_area_km2(geom: Polygon | MultiPolygon) -> float:
    area_m2, _ = _GEOD.geometry_area_perimeter(geom)
    return abs(area_m2) / 1e6


def aoi_hash(geom: Polygon | MultiPolygon) -> str:
    """Stable short hash of the geometry (coordinates rounded to ~0.1 m).

    Normalising first means the same area always gives the same hash regardless of vertex
    order or starting point, so cache hits are reliable.
    """
    rounded = shapely.set_precision(shapely.normalize(geom), 1e-6)
    return hashlib.sha256(rounded.wkb).hexdigest()[:16]


def _from_bbox(values: Any) -> Polygon:
    try:
        min_lon, min_lat, max_lon, max_lat = (float(v) for v in values)
    except (TypeError, ValueError) as exc:
        raise AOIError("bbox must be 4 numbers: [min_lon, min_lat, max_lon, max_lat]") from exc
    if not (-180 <= min_lon <= 180 and -180 <= max_lon <= 180):
        raise AOIError("bbox longitudes must be between -180 and 180")
    if not (-90 <= min_lat <= 90 and -90 <= max_lat <= 90):
        raise AOIError("bbox latitudes must be between -90 and 90")
    if min_lon >= max_lon or min_lat >= max_lat:
        raise AOIError(
            "bbox order must be [min_lon, min_lat, max_lon, max_lat] with min < max "
            "(boxes crossing the antimeridian are not supported)"
        )
    return box(min_lon, min_lat, max_lon, max_lat)


def _from_geojson(obj: dict[str, Any]) -> Polygon | MultiPolygon:
    kind = obj.get("type")
    if kind == "FeatureCollection":
        feats = obj.get("features") or []
        if not feats:
            raise AOIError("GeoJSON FeatureCollection is empty")
        geoms = [_from_geojson(f) for f in feats]
        return shapely.union_all(geoms)  # type: ignore[return-value]
    if kind == "Feature":
        if not obj.get("geometry"):
            raise AOIError("GeoJSON Feature has no geometry")
        return _from_geojson(obj["geometry"])
    try:
        geom = shape(obj)
    except Exception as exc:  # shapely raises several types for malformed input
        raise AOIError(f"Could not read GeoJSON geometry: {exc}") from exc
    if not isinstance(geom, Polygon | MultiPolygon):
        raise AOIError(f"AOI must be a Polygon or MultiPolygon, got {geom.geom_type}")
    return geom


def parse_aoi(
    spec: Any,
    max_area_km2: float,
    min_area_km2: float,
    max_geojson_bytes: int = 5_000_000,
) -> AOI:
    """Turn a bbox list, GeoJSON dict, GeoJSON file path or shapely geometry into an AOI."""
    if isinstance(spec, Polygon | MultiPolygon):
        geom = spec
    elif isinstance(spec, list | tuple):
        geom = _from_bbox(spec)
    elif isinstance(spec, dict):
        geom = _from_geojson(spec)
    elif isinstance(spec, str | Path):
        path = Path(spec)
        if path.suffix.lower() not in {".geojson", ".json"} or not path.is_file():
            raise AOIError(f"AOI file must be an existing .geojson/.json file: {path.name}")
        if path.stat().st_size > max_geojson_bytes:
            raise AOIError(f"AOI file is larger than {max_geojson_bytes // 1_000_000} MB")
        try:
            geom = _from_geojson(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError as exc:
            raise AOIError(f"AOI file is not valid JSON: {exc}") from exc
    else:
        raise AOIError(f"Unsupported AOI input type: {type(spec).__name__}")

    if geom.is_empty:
        raise AOIError("AOI geometry is empty")
    minx, miny, maxx, maxy = geom.bounds
    if not (-180 <= minx and maxx <= 180 and -90 <= miny and maxy <= 90):
        raise AOIError("AOI coordinates must be WGS84 lon/lat degrees")
    if maxx - minx > 180:
        raise AOIError("AOI spans more than 180° of longitude (antimeridian not supported)")
    if not geom.is_valid:
        repaired = shapely.make_valid(geom)
        polys = [g for g in getattr(repaired, "geoms", [repaired]) if isinstance(g, Polygon)]
        if not polys:
            raise AOIError("AOI polygon is invalid (e.g. self-intersecting) and cannot be repaired")
        geom = polys[0] if len(polys) == 1 else MultiPolygon(polys)

    area = geodesic_area_km2(geom)
    if area > max_area_km2:
        raise AOIError(
            f"AOI is {area:,.0f} km², larger than the {max_area_km2:,.0f} km² limit. "
            "Please choose a smaller area (e.g. one valley reach)."
        )
    if area < min_area_km2:
        raise AOIError(f"AOI is {area:.2f} km², smaller than the {min_area_km2} km² minimum.")

    centroid = geom.centroid
    return AOI(
        geometry=geom,
        area_km2=area,
        utm_epsg=utm_epsg_for(centroid.x, centroid.y),
        aoi_hash=aoi_hash(geom),
    )
