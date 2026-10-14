"""Copernicus Data Space check + Sentinel-1 inventory for an event (milestone M0).

    python scripts/check_cdse.py [--event configs/events/trishuli_2026.yaml] [--no-dem]

1. Lists every Sentinel-1 scene over the AOI from ~6 weeks before to ~5 weeks after the
   event (public catalogue, no login), grouped by track (relative orbit + pass direction),
   showing which tracks have both pre- and post-event images.
2. If you are logged in (`python -m sampark login`), reads the Copernicus DEM elevation at
   the centre of the focus area through openEO, proving processing access works.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from collections import defaultdict
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

from sampark.aoi import parse_aoi
from sampark.catalogue import search_s1_scenes
from sampark.config import REPO_ROOT, load_config, load_event


def _inventory(event, aoi, config) -> None:
    start = datetime.combine(event.event_date - timedelta(days=42), time(0), tzinfo=UTC)
    end = datetime.combine(event.event_date + timedelta(days=36), time(0), tzinfo=UTC)
    scenes = search_s1_scenes(aoi.bounds, start, end, timeout_s=config.network.timeout_s,
                              retries=config.network.retries)
    print(f"Sentinel-1 scenes over AOI {aoi.bounds}, {start:%Y-%m-%d} .. {end:%Y-%m-%d}: "
          f"{len(scenes)}  (source: Copernicus Data Space catalogue)")
    if not scenes:
        print("No scenes found for this area/period.")
        return
    print(f"Platforms: {sorted({s.platform for s in scenes})}   "
          f"Modes: {sorted({s.mode for s in scenes})}")

    event_day = event.event_date.isoformat()
    tracks = defaultdict(list)
    for s in scenes:
        tracks[s.track].append(s)
    for (rel_orbit, state), items in sorted(tracks.items()):
        days = sorted({s.acquired.strftime("%Y-%m-%d %H:%MZ") for s in items})
        pre = [d for d in days if d[:10] < event_day]
        post = [d for d in days if d[:10] > event_day]
        flag = "pre+post OK" if pre and post else "missing pre or post"
        print(f"\n  Track {rel_orbit:>3} {state:<10} {len(pre)} pre / {len(post)} post -> {flag}")
        for d in days:
            tag = "pre " if d[:10] < event_day else ("post" if d[:10] > event_day else "EVENT DAY")
            pols = sorted({"+".join(s.polarizations) for s in items
                           if s.acquired.strftime("%Y-%m-%d %H:%MZ") == d})
            print(f"     {tag:<9} {d}  {', '.join(pols)}")


def _dem_sample(event, aoi) -> None:
    import rasterio

    from sampark.cdse_client import CDSEError, connect, load_collection

    try:
        conn = connect()
    except CDSEError as exc:
        print(f"\nCDSE processing: not logged in — skipping DEM sample.\n{exc}")
        return
    fb = event.focus_bbox or aoi.bounds
    lon, lat = (fb[0] + fb[2]) / 2, (fb[1] + fb[3]) / 2
    extent = {"west": lon - 0.002, "south": lat - 0.002, "east": lon + 0.002, "north": lat + 0.002}
    cube = load_collection(conn, "COPERNICUS_30", spatial_extent=extent).max_time()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "dem.tif"
        cube.download(str(path), format="GTiff")
        with rasterio.open(path) as src:
            band = src.read(1)
    value = float(band[band.shape[0] // 2, band.shape[1] // 2])
    print(f"\nCDSE processing: OK — Copernicus DEM GLO-30 elevation near ({lon:.4f}, {lat:.4f}): "
          f"{value:.0f} m")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--event", type=Path,
                        default=REPO_ROOT / "configs" / "events" / "trishuli_2026.yaml")
    parser.add_argument("--no-dem", action="store_true", help="skip the openEO DEM sample")
    args = parser.parse_args()

    event = load_event(args.event)
    config = load_config(event)
    aoi = parse_aoi(list(event.aoi.bbox) if event.aoi.bbox else event.aoi.geojson,
                    config.limits.max_aoi_km2, config.limits.min_aoi_km2)
    _inventory(event, aoi, config)
    if not args.no_dem:
        _dem_sample(event, aoi)
    return 0


if __name__ == "__main__":
    sys.exit(main())
