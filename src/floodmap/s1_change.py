"""Sentinel-1 before/after images from Copernicus Data Space + local change computation.

Server side (CDSE openEO), ONE request per area:
  load SENTINEL1_GRD of the chosen track only (relative orbit + pass direction filter)
  -> sar_backscatter: calibrated sigma0, terrain-corrected with the Copernicus DEM
  -> resample to the local UTM grid
  -> per-pixel median of the pre-event images + the post-event pass -> one GeoTIFF
  Fallback if that fails: one request per image, one at a time (resumable).
Local (numpy, deterministic, testable):
  speckle filter -> dB -> log-ratio -> validity mask
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import Resampling, reproject
from scipy import ndimage

from floodmap.aoi import AOI
from floodmap.cdse_client import load_collection
from floodmap.s1_pairing import Acquisition, TrackPair, to_utm

log = logging.getLogger("floodmap.s1")

TIME_MARGIN = timedelta(minutes=10)  # around the pass, so only this pass is loaded


def analysis_resolution(aoi: AOI, resolution_m: float, max_pixels: int) -> float:
    """Pixel size: the configured one, coarser if the area would exceed `max_pixels`."""
    minx, miny, maxx, maxy = to_utm(aoi.geometry, aoi.utm_epsg).bounds
    area = (maxx - minx) * (maxy - miny)
    needed = math.sqrt(area / max_pixels)
    return float(max(resolution_m, math.ceil(needed / 10.0) * 10.0))


def _openeo_time(when) -> str:
    """openEO accepts 'YYYY-MM-DDTHH:MM:SSZ' (UTC, no fractional seconds or offsets)."""
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _download(cube, out_path: Path, max_wait_retries: int = 6) -> Path:
    """Synchronous download; waits and retries when CDSE answers 429 (too many requests).

    The file is written under a temporary name and renamed only when complete, so an
    interrupted download never looks like a finished one (safe resume).
    """
    part = out_path.with_name(out_path.name + ".part")
    for attempt in range(max_wait_retries + 1):
        try:
            cube.download(str(part), format="GTiff")
            part.replace(out_path)
            return out_path
        except Exception as exc:
            if getattr(exc, "http_status_code", None) != 429 or attempt == max_wait_retries:
                part.unlink(missing_ok=True)
                raise
            wait = 10 * (attempt + 1)
            log.warning("CDSE busy (429 Too Many Requests); retrying in %ss", wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _midnight(day) -> datetime:
    return datetime.combine(day, datetime.min.time(), tzinfo=UTC)


def _s1_cube(conn, aoi: AOI, bands: list[str], start, end, properties: dict, resolution: float):
    w, s, e, n = aoi.bounds
    cube = load_collection(
        conn, "SENTINEL1_GRD",
        spatial_extent={"west": w, "south": s, "east": e, "north": n},
        temporal_extent=[_openeo_time(start), _openeo_time(end)],
        bands=bands,
        properties=properties,
    )
    # Calibrated backscatter (sigma0), geocoded with terrain correction using Copernicus DEM.
    cube = cube.sar_backscatter(coefficient="sigma0-ellipsoid", elevation_model="COPERNICUS_30")
    return cube.resample_spatial(resolution=resolution, projection=aoi.utm_epsg)


def fetch_pair_composite(conn, aoi: AOI, pair: TrackPair, bands: list[str], resolution: float,
                         out_path: Path) -> Path:
    """ONE request: before-median and after image, computed on CDSE, as one GeoTIFF.

    Bands: pre_<b>... (per-pixel median of the pre images), post_<b>... (the post pass).
    Only images of the chosen track are loaded (relative orbit + pass direction filter).
    """
    orbit, state = pair.relative_orbit, pair.orbit_state
    # CDSE labels the time axis per DAY (00:00), so windows must be whole days. That is
    # safe: the orbit filter already guarantees only images of this one track are loaded,
    # and one track passes over an area at most once per day.
    pre_start, pre_end = pair.pre[-1].day, pair.pre[0].day + timedelta(days=1)
    post_start, post_end = pair.post.day, pair.post.day + timedelta(days=1)
    cube = _s1_cube(conn, aoi, bands, _midnight(pre_start), _midnight(post_end),
                    {"sat:relative_orbit": lambda v: v == orbit,
                     "sat:orbit_state": lambda v: v == state}, resolution)
    pre = (cube.filter_temporal(pre_start.isoformat(), pre_end.isoformat())
           .reduce_dimension(dimension="t", reducer="median")
           .rename_labels("bands", [f"pre_{b}" for b in bands]))
    post = (cube.filter_temporal(post_start.isoformat(), post_end.isoformat())
            .reduce_dimension(dimension="t", reducer="mean")  # merges slices of one pass
            .rename_labels("bands", [f"post_{b}" for b in bands]))
    return _download(pre.merge_cubes(post), out_path)


def fetch_acquisition(conn, aoi: AOI, acq: Acquisition, bands: list[str], resolution: float,
                      out_path: Path) -> Path:
    """Fallback: download one acquisition (narrow time window around the pass)."""
    if out_path.exists():  # finished earlier (files are renamed only when complete)
        return out_path
    orbit, state = acq.relative_orbit, acq.orbit_state
    cube = _s1_cube(conn, aoi, bands, acq.start - TIME_MARGIN, acq.end + TIME_MARGIN,
                    {"sat:relative_orbit": lambda v: v == orbit,
                     "sat:orbit_state": lambda v: v == state}, resolution)
    cube = cube.reduce_dimension(dimension="t", reducer="mean")
    return _download(cube, out_path)


def fetch_pair_separately(conn, aoi: AOI, pair: TrackPair, bands: list[str], resolution: float,
                          folder: Path, progress=None) -> tuple[Path, list[Path]]:
    """Fallback: one request per image, one at a time (CDSE free-tier concurrency)."""
    jobs = [("post", pair.post, folder / "post.tif")]
    jobs += [(f"pre_{i + 1}", acq, folder / f"pre_{i + 1}.tif") for i, acq in enumerate(pair.pre)]
    for name, acq, path in jobs:
        if progress:
            progress("s1", f"downloading {name} image {acq.start:%Y-%m-%d %H:%M}Z from CDSE")
        fetch_acquisition(conn, aoi, acq, bands, resolution, path)
    return jobs[0][2], [p for _, _, p in jobs[1:]]


# ---------------------------------------------------------------------------------------
# Local processing
# ---------------------------------------------------------------------------------------
@dataclass
class ChangeLayers:
    pre_vv_db: np.ndarray
    post_vv_db: np.ndarray
    lr_vv: np.ndarray
    lr_vh: np.ndarray | None
    valid: np.ndarray
    transform: rasterio.Affine
    crs: rasterio.crs.CRS
    n_pre: int


def read_bands(path: Path, ref: rasterio.io.DatasetReader | None = None) -> dict[str, np.ndarray]:
    """Read a GeoTIFF as {band name: float32 array}, aligned to `ref`'s grid if given."""
    with rasterio.open(path) as src:
        names = [d or f"b{i + 1}" for i, d in enumerate(src.descriptions)]
        if ref is None or (src.shape == ref.shape and src.transform == ref.transform):
            return {nm: src.read(i + 1).astype("float32") for i, nm in enumerate(names)}
        out = {}
        for i, nm in enumerate(names):  # different grid: resample onto the reference grid
            dst = np.full(ref.shape, np.nan, dtype="float32")
            reproject(src.read(i + 1).astype("float32"), dst, src_transform=src.transform,
                      src_crs=src.crs, dst_transform=ref.transform, dst_crs=ref.crs,
                      resampling=Resampling.bilinear, src_nodata=np.nan, dst_nodata=np.nan)
            out[nm] = dst
        return out


def speckle_filter(linear: np.ndarray, window: int) -> np.ndarray:
    """Median filter in linear power; no-data stays no-data.

    Speckle is the grainy noise of radar images. A median keeps edges (river banks) sharper
    than a mean. No-data pixels are filled with 0 during filtering, which can only lower
    values right next to image borders; they are masked out again afterwards.
    """
    if window <= 1:
        return linear
    nan = ~np.isfinite(linear)
    filtered = ndimage.median_filter(np.where(nan, 0.0, linear), size=window)
    filtered[nan] = np.nan
    return filtered


def to_db(linear: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        return (10.0 * np.log10(np.where(linear > 0, linear, np.nan))).astype("float32")


LinearImage = dict[str, np.ndarray]  # band name -> linear backscatter


def load_composite(path: Path, bands: list[str]) -> tuple[LinearImage, LinearImage, object, object]:
    """Read the one-request GeoTIFF: pre_<band>... then post_<band>... ."""
    expected = [f"pre_{b}" for b in bands] + [f"post_{b}" for b in bands]
    with rasterio.open(path) as src:
        names = list(src.descriptions)
        if sorted(n or "" for n in names) != sorted(expected):
            names = expected  # unnamed bands: merge_cubes keeps pre bands before post bands
        data = {nm: src.read(i + 1).astype("float32") for i, nm in enumerate(names)}
        transform, crs = src.transform, src.crs
    pre = {b: data[f"pre_{b}"] for b in bands}
    post = {b: data[f"post_{b}"] for b in bands}
    return pre, post, transform, crs


def load_separate(post_path: Path, pre_paths: list[Path]):
    """Read per-image GeoTIFFs (fallback path) and take the per-pixel median of the pres."""
    import warnings

    with rasterio.open(post_path) as ref:
        post = read_bands(post_path)
        pres = [read_bands(p, ref) for p in pre_paths]
        transform, crs = ref.transform, ref.crs
    pre: LinearImage = {}
    for band in post:
        if all(band in p for p in pres):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN pixels at borders
                pre[band] = np.nanmedian(np.stack([p[band] for p in pres]), axis=0)
    return pre, post, transform, crs


def change_layers(pre: LinearImage, post: LinearImage, transform, crs, speckle_window: int,
                  min_valid_db: float, n_pre: int) -> ChangeLayers:
    """Speckle filter both images, convert to dB, compute the log-ratio and validity mask."""
    pre_vv = to_db(speckle_filter(pre["VV"], speckle_window))
    post_vv = to_db(speckle_filter(post["VV"], speckle_window))
    has_vh = "VH" in pre and "VH" in post
    pre_vh = to_db(speckle_filter(pre["VH"], speckle_window)) if has_vh else None
    post_vh = to_db(speckle_filter(post["VH"], speckle_window)) if has_vh else None

    valid = (np.isfinite(pre_vv) & np.isfinite(post_vv)
             & (pre_vv > min_valid_db) & (post_vv > min_valid_db))
    lr_vv = np.where(valid, post_vv - pre_vv, np.nan).astype("float32")
    lr_vh = None
    if has_vh:
        lr_vh = np.where(valid & np.isfinite(pre_vh) & np.isfinite(post_vh),
                         post_vh - pre_vh, np.nan).astype("float32")
    return ChangeLayers(pre_vv, post_vv, lr_vv, lr_vh, valid, transform, crs, n_pre)


def write_geotiff(path: Path, layers: dict[str, np.ndarray], transform, crs) -> Path:
    """Write named float32/uint8 layers as one multi-band, compressed GeoTIFF."""
    arrays = list(layers.values())
    dtype = arrays[0].dtype
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", height=arrays[0].shape[0],
                       width=arrays[0].shape[1], count=len(arrays), dtype=dtype, crs=crs,
                       transform=transform, compress="deflate",
                       nodata=np.nan if np.issubdtype(dtype, np.floating) else 0) as dst:
        for i, (name, arr) in enumerate(layers.items(), start=1):
            dst.write(arr, i)
            dst.set_band_description(i, name)
    return path
