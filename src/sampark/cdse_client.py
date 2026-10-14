"""Copernicus Data Space Ecosystem (CDSE) processing access via openEO.

- `login()` (interactive, once): the openEO client prints a link + code; you approve it in
  your browser. The client stores a refresh token in YOUR USER PROFILE (outside this
  repository), so it can never be committed.
- `connect()` (non-interactive): reuses that stored token. Used by the pipeline and `doctor`;
  it never opens a login prompt in the middle of a run.
- `load_collection()` is the only way pipeline code opens a data collection; the id is
  checked against the data-rules allowlist first.
"""

from __future__ import annotations

import logging

from sampark.data_rules import CDSE_OPENEO_URL, check_openeo_collection
from sampark.logging_setup import redact

log = logging.getLogger("sampark.cdse")

REGISTER_URL = "https://dataspace.copernicus.eu"


class CDSEError(RuntimeError):
    """CDSE processing is not usable; message says how to fix it."""


def _openeo():
    import openeo  # imported lazily so offline tools and tests don't need a connection

    return openeo


def login():
    """Interactive login (device-code flow). Returns an authenticated connection."""
    try:
        conn = _openeo().connect(CDSE_OPENEO_URL)
        conn.authenticate_oidc()  # uses a stored refresh token, else device-code flow
    except Exception as exc:
        raise CDSEError(f"Copernicus Data Space login failed: {redact(str(exc))[:300]}") from exc
    return conn


def connect():
    """Authenticated connection using the stored refresh token (no prompts)."""
    try:
        conn = _openeo().connect(CDSE_OPENEO_URL)
        conn.authenticate_oidc_refresh_token()
    except Exception as exc:
        raise CDSEError(
            "Not logged in to Copernicus Data Space (or the login expired): "
            f"{redact(str(exc))[:200]}\n"
            f"Fix: create a free account at {REGISTER_URL}, then run "
            "`python -m sampark login` once."
        ) from exc
    return conn


def load_collection(conn, collection_id: str, **kwargs):
    """Allowlisted wrapper around openEO `load_collection`."""
    return conn.load_collection(check_openeo_collection(collection_id), **kwargs)


# Transient problems worth waiting for: CDSE busy (429) or briefly failing (5xx), and broken
# or timed-out connections (seen 2026-10-13: "Connection broken: IncompleteRead" in the
# middle of a Copernicus DEM download, which failed the whole terrain stage).
BUSY_STATUS = {429}
SERVER_STATUS = {500, 502, 503, 504}
BUSY_RETRIES, NETWORK_RETRIES = 6, 3


def _transient(exc: Exception) -> str | None:
    status = getattr(exc, "http_status_code", None)
    if status in BUSY_STATUS:
        return "busy"
    if status in SERVER_STATUS:
        return "network"
    from sampark.net import TRANSIENT_NETWORK_ERRORS

    if isinstance(exc, TRANSIENT_NETWORK_ERRORS):
        return "network"
    return None


def download(cube, out_path, sleep=None):
    """Synchronous openEO download with safe retries for transient problems only.

    The file is written under a temporary name and renamed only when complete, so an
    interrupted download never looks like a finished one. Anything else (bad request, no
    data, login problem) is raised at once with its own message."""
    import time
    from pathlib import Path

    sleep = sleep or time.sleep
    out_path = Path(out_path)
    part = out_path.with_name(out_path.name + ".part")
    tries = {"busy": 0, "network": 0}
    while True:
        try:
            cube.download(str(part), format="GTiff")
            part.replace(out_path)
            return out_path
        except Exception as exc:
            part.unlink(missing_ok=True)
            kind = _transient(exc)
            limit = BUSY_RETRIES if kind == "busy" else NETWORK_RETRIES
            if kind is None or tries[kind] >= limit:
                raise
            tries[kind] += 1
            wait = (10 if kind == "busy" else 15) * tries[kind]
            log.warning("CDSE %s (%s); retry %d of %d in %ss", "busy" if kind == "busy"
                        else "connection/server problem", type(exc).__name__, tries[kind],
                        limit, wait)
            sleep(wait)
