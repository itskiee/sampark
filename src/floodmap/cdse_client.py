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

from floodmap.data_rules import CDSE_OPENEO_URL, check_openeo_collection
from floodmap.logging_setup import redact

log = logging.getLogger("floodmap.cdse")

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
            "`python -m floodmap login` once."
        ) from exc
    return conn


def load_collection(conn, collection_id: str, **kwargs):
    """Allowlisted wrapper around openEO `load_collection`."""
    return conn.load_collection(check_openeo_collection(collection_id), **kwargs)
