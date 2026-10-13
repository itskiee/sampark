"""Copernicus downloads must survive transient problems and fail fast on real errors.

Seen 2026-10-13 (Kathmandu test): "Connection broken: IncompleteRead" in the middle of a
Copernicus DEM download failed the whole terrain stage, because only the radar download
retried, and only on "busy" (HTTP 429).
"""

from __future__ import annotations

import pytest
import requests

from floodmap.cdse_client import NETWORK_RETRIES, download


class FakeCube:
    """Fails with the given exceptions first, then writes the file."""

    def __init__(self, errors):
        self.errors, self.calls = list(errors), 0

    def download(self, path, format):  # noqa: A002 - same signature as openEO
        self.calls += 1
        with open(path, "wb") as fh:
            fh.write(b"partial")  # a broken download leaves a partial file behind
        if self.errors:
            raise self.errors.pop(0)


class ApiError(Exception):
    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.http_status_code = status


def test_broken_connection_is_retried_and_succeeds(tmp_path):
    waits = []
    cube = FakeCube([requests.exceptions.ChunkedEncodingError("IncompleteRead"),
                     requests.exceptions.ConnectionError("reset")])
    out = download(cube, tmp_path / "dem.tif", sleep=waits.append)
    assert out.is_file() and cube.calls == 3 and len(waits) == 2
    assert not (tmp_path / "dem.tif.part").exists()


def test_busy_and_server_errors_are_retried(tmp_path):
    cube = FakeCube([ApiError(429), ApiError(503)])
    assert download(cube, tmp_path / "s2.tif", sleep=lambda s: None).is_file()


def test_real_errors_fail_at_once_without_leftovers(tmp_path):
    cube = FakeCube([ApiError(400)])
    with pytest.raises(ApiError):
        download(cube, tmp_path / "x.tif", sleep=lambda s: None)
    assert cube.calls == 1
    assert not (tmp_path / "x.tif").exists() and not (tmp_path / "x.tif.part").exists()


def test_retries_are_limited(tmp_path):
    cube = FakeCube([requests.exceptions.Timeout("slow")] * (NETWORK_RETRIES + 1))
    with pytest.raises(requests.exceptions.Timeout):
        download(cube, tmp_path / "y.tif", sleep=lambda s: None)
    assert cube.calls == NETWORK_RETRIES + 1
    assert not (tmp_path / "y.tif").exists()
