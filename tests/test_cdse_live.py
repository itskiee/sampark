"""Live Copernicus Data Space checks (opt-in).

    pytest -m network   # public catalogue, no login
    pytest -m cdse      # openEO processing, needs `python -m floodmap login`
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from floodmap.catalogue import search_s1_scenes

TRISHULI = (85.10, 27.85, 85.45, 28.32)


@pytest.mark.network
def test_catalogue_has_same_track_pairs_around_the_event():
    scenes = search_s1_scenes(
        TRISHULI, datetime(2026, 8, 1, tzinfo=UTC), datetime(2026, 9, 20, tzinfo=UTC),
        timeout_s=60, retries=1,
    )
    assert scenes, "no Sentinel-1 scenes returned"
    event = datetime(2026, 8, 26, tzinfo=UTC)
    tracks = {s.track for s in scenes}
    paired = [t for t in tracks
              if any(s.track == t and s.acquired < event for s in scenes)
              and any(s.track == t and s.acquired > event for s in scenes)]
    assert paired, f"no track with both pre- and post-event scenes: {tracks}"


@pytest.mark.cdse
def test_copernicus_dem_via_openeo():
    import rasterio

    from floodmap.cdse_client import connect, load_collection

    conn = connect()
    extent = {"west": 85.348, "south": 28.158, "east": 85.352, "north": 28.162}
    cube = load_collection(conn, "COPERNICUS_30", spatial_extent=extent).max_time()
    path = cube.download(format="GTiff")  # returns bytes when no path is given
    assert path, "empty DEM download"
    with rasterio.MemoryFile(path) as mem, mem.open() as src:
        values = src.read(1)
    assert 500 < float(values.mean()) < 5000
