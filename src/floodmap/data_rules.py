"""Runtime enforcement of the hackathon data rules (breaking them = disqualification).

Allowed inputs (official brief): Sentinel-1, Sentinel-2, Copernicus DEM, OpenStreetMap as
it was BEFORE the event (via the ohsome API), and the listed training datasets.
Data sources follow the brief literally: Sentinel data from the Copernicus Data Space
Ecosystem (dataspace.copernicus.eu, the source named in the brief's dataset table),
OpenStreetMap from the ohsome API (also named there).

This module holds ONLY the positive allowlist. Every data collection and every HTTP request
made by the pipeline goes through the `check_*` functions below, so a non-allowed source
raises `DataRuleViolation` instead of silently being used. The list of known-forbidden
sources lives in tests/ (a static scanner), so pipeline code never contains those names.

Data-source endpoints are code constants on purpose, not config values: nobody can point
the pipeline at a different source by editing a YAML file.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import urlsplit

# --- Copernicus Data Space Ecosystem (CDSE) -------------------------------------------
# openEO processing endpoint of CDSE itself. Deliberately NOT the "openeofed" federation
# endpoint, which may route work to partner platforms outside CDSE.
CDSE_OPENEO_URL = "https://openeo.dataspace.copernicus.eu"
# Public STAC catalogue of CDSE (scene search, no login needed).
CDSE_STAC_URL = "https://stac.dataspace.copernicus.eu/v1"

# openEO collections that are the allowed missions.
ALLOWED_OPENEO_COLLECTIONS: frozenset[str] = frozenset(
    {
        "SENTINEL1_GRD",  # Sentinel-1 Ground Range Detected
        "SENTINEL2_L2A",  # Sentinel-2 Level-2A surface reflectance (incl. SCL band)
        "COPERNICUS_30",  # Copernicus DEM GLO-30
    }
)
# STAC catalogue collections (scene metadata of the same missions).
ALLOWED_STAC_COLLECTIONS: frozenset[str] = frozenset({"sentinel-1-grd", "sentinel-2-l2a"})

# --- OpenStreetMap (pre-event) ---------------------------------------------------------
OHSOME_API_URL = "https://api.ohsome.org/v1"

# Hosts our own HTTP client (floodmap/net.py) may call. The openEO client library talks to
# CDSE_OPENEO_URL and the CDSE login service itself; we only ever hand it CDSE_OPENEO_URL.
# The LLM provider host is added in milestone M5.
ALLOWED_HTTP_HOSTS: frozenset[str] = frozenset({"api.ohsome.org", "stac.dataspace.copernicus.eu"})

# Latest OSM data in ohsome, checked on 2026-10-06 via GET /v1/metadata
# (extractRegion.temporalExtent.toTimestamp). Used only as a fallback when the live
# metadata cannot be fetched; the live value is preferred.
OHSOME_LATEST_KNOWN = datetime(2026, 7, 27, 9, 0, tzinfo=UTC)


class DataRuleViolation(RuntimeError):
    """Raised when code tries to use a data source the hackathon rules do not allow."""


def check_openeo_collection(collection_id: str) -> str:
    """Return `collection_id` unchanged if it is allowed, otherwise raise DataRuleViolation."""
    if collection_id not in ALLOWED_OPENEO_COLLECTIONS:
        raise DataRuleViolation(
            f"openEO collection '{collection_id}' is not an allowed input. "
            f"Allowed: {sorted(ALLOWED_OPENEO_COLLECTIONS)}"
        )
    return collection_id


def check_stac_collection(collection_id: str) -> str:
    if collection_id not in ALLOWED_STAC_COLLECTIONS:
        raise DataRuleViolation(
            f"STAC collection '{collection_id}' is not allowed. "
            f"Allowed: {sorted(ALLOWED_STAC_COLLECTIONS)}"
        )
    return collection_id


def check_http_url(url: str) -> str:
    """Return `url` unchanged if it is HTTPS to an allowed host, otherwise raise."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or host not in ALLOWED_HTTP_HOSTS:
        raise DataRuleViolation(
            f"HTTP request to '{parts.scheme}://{host}' is not allowed. "
            f"Allowed hosts (HTTPS only): {sorted(ALLOWED_HTTP_HOSTS)}"
        )
    return url


def ohsome_snapshot(event_date: date, latest_available: datetime | None = None) -> datetime:
    """OSM snapshot timestamp to use for an event: OSM "as it was before the event".

    Rule: 00:00 UTC on the day BEFORE the event, capped at the newest data ohsome has.
    Using the day before (not the event day) keeps a safety margin for time zones:
    00:00 UTC is 05:45 in Nepal, still the day before any event on `event_date`.

    Works for any event, not just 2026: a 2021 flood gets a 2021 snapshot.
    """
    latest = latest_available or OHSOME_LATEST_KNOWN
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=UTC)
    day_before = datetime.combine(event_date - timedelta(days=1), time(0, 0), tzinfo=UTC)
    snapshot = min(day_before, latest)

    event_start = datetime.combine(event_date, time(0, 0), tzinfo=UTC)
    if snapshot >= event_start:  # defensive: cannot happen with the rule above
        raise DataRuleViolation(f"OSM snapshot {snapshot} is not before event {event_date}")
    return snapshot
