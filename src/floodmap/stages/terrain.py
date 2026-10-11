"""Stage "terrain": Copernicus DEM masks on the radar grid.

Produces, on the same grid as the Sentinel-1 change layers:
  hand_m            height above nearest drainage (metres)
  slope_deg         terrain slope
  plausible         1 where flood/debris is physically plausible (low HAND, not too steep)
  geom_ok_<track>   1 where that track has no radar layover/shadow
  permanent_water   1 where the area was already water BEFORE the event
plus, on the DEM grid, flow directions and accumulation (used by the flood-path bonus).
"""

from __future__ import annotations

import logging

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import rasterio  # noqa: E402

from floodmap import attribution  # noqa: E402
from floodmap.cdse_client import connect  # noqa: E402
from floodmap.pipeline import StageContext, register_stage  # noqa: E402
from floodmap.results import StageResult, StageStatus  # noqa: E402
from floodmap.s1_change import write_geotiff  # noqa: E402
from floodmap.terrain import (  # noqa: E402
    DEFAULT_INCIDENCE_DEG,
    LOOK_AZIMUTH_DEG,
    fetch_dem,
    hydrology,
    layover_shadow,
    slope_and_gradients,
    to_grid,
)
from floodmap.thresholds import remove_small_patches  # noqa: E402

log = logging.getLogger("floodmap.stages.terrain")


@register_stage("terrain", requires=("s1",))
def run_terrain(ctx: StageContext) -> StageResult:
    cfg, aoi = ctx.config.terrain, ctx.aoi
    s1 = ctx.results.stages["s1"].outputs
    warnings: list[str] = []

    # 1. DEM (cached) + hydrology (cached) --------------------------------------------
    key = {"buffer_m": cfg.buffer_m, "res": cfg.dem_resolution_m, "epsg": aoi.utm_epsg,
           "bounds": aoi.bounds, "collection": "COPERNICUS_30"}
    folder = ctx.cache.stage_dir(aoi.aoi_hash, ctx.event_date, "dem", key)
    dem_path = folder / "dem.tif"
    if not ctx.cache.is_complete(folder):
        folder.mkdir(parents=True, exist_ok=True)
        ctx.progress("terrain", "downloading Copernicus DEM GLO-30 from CDSE")
        fetch_dem(connect(), aoi, cfg.buffer_m, cfg.dem_resolution_m, dem_path)
        ctx.cache.mark_complete(folder, key)
    hydro_path = folder / f"hydrology_stream{cfg.stream_area_km2:g}km2.tif"
    with rasterio.open(dem_path) as src:
        dem = src.read(1).astype("float32")
        dem_transform, crs, dem_res = src.transform, src.crs, src.res[0]
    if hydro_path.exists():
        ctx.progress("terrain", "using cached flow directions / HAND")
        with rasterio.open(hydro_path) as src:
            fdir, acc, hand_dem = (src.read(i) for i in (1, 2, 3))
        fdir = fdir.astype("int32")
    else:
        ctx.progress("terrain", "computing flow directions, accumulation and HAND (pysheds)")
        h = hydrology(dem_path, cfg.stream_area_km2)
        fdir, acc, hand_dem = h.fdir, h.acc, h.hand
        write_geotiff(hydro_path, {"fdir": fdir.astype("float32"), "acc": acc,
                                   "hand_m": hand_dem}, dem_transform, crs)
    slope_dem, dz_dx, dz_dy = slope_and_gradients(dem, dem_res)

    # 2. Radar grid (from the primary track's change layers) ---------------------------
    with rasterio.open(s1["files"]["change"]) as src:
        shape, transform = src.shape, src.transform
        pre_vv, pre_vh = src.read(1), src.read(5)
    hand = to_grid(hand_dem, dem_transform, shape, transform, crs, categorical=False)
    slope = to_grid(slope_dem, dem_transform, shape, transform, crs, categorical=False)
    plausible = np.isfinite(hand) & (hand <= cfg.hand_max_m) & (slope <= cfg.slope_max_deg)

    # 3. Layover / shadow per track ----------------------------------------------------
    layers = {"hand_m": hand, "slope_deg": slope, "plausible": plausible.astype("float32")}
    geometry = {}
    for track in s1["tracks"]:
        state = track["pair"]["orbit_state"]
        inc = track.get("incidence_angle") or DEFAULT_INCIDENCE_DEG
        lay, sha = layover_shadow(dz_dx, dz_dy, LOOK_AZIMUTH_DEG[state], inc)
        bad = to_grid((lay | sha).astype("uint8"), dem_transform, shape, transform, crs,
                      categorical=True).astype(bool)
        tag = f"track{track['pair']['relative_orbit']}_{state}"
        layers[f"geom_ok_{tag}"] = (~bad).astype("float32")
        inside = np.isfinite(hand)
        geometry[tag] = {
            "look_azimuth_deg": LOOK_AZIMUTH_DEG[state], "incidence_deg": round(inc, 1),
            "layover_shadow_share": round(float(bad[inside].mean()), 3),
            "layover_shadow_share_in_plausible_zone": round(float(bad[plausible].mean()), 3)
            if plausible.any() else None,
        }

    # 4. Permanent water (before the event) -------------------------------------------
    perm = np.isfinite(pre_vv) & (pre_vv <= cfg.permanent_water_vv_db) & plausible
    if np.isfinite(pre_vh).any():
        perm &= np.isfinite(pre_vh) & (pre_vh <= cfg.permanent_water_vh_db)
    perm = remove_small_patches(perm, 5)
    layers["permanent_water"] = perm.astype("float32")

    out = ctx.results.run_dir / "terrain"
    terrain_tif = write_geotiff(out / "terrain_radar_grid.tif", layers, transform, crs)
    hydro_tif = write_geotiff(out / "hydrology_dem_grid.tif",
                              {"fdir": fdir.astype("float32"), "acc": acc, "hand_m": hand_dem,
                               "dem_m": dem}, dem_transform, crs)
    png = _render(hand, slope, plausible, layers, perm, transform, out / "terrain.png",
                  ctx.results.event_name)

    px_km2 = abs(transform.a * transform.e) / 1e6
    valid = np.isfinite(hand)
    stats = {
        "plausible_zone_km2": round(float(plausible.sum()) * px_km2, 2),
        "plausible_share": round(float(plausible[valid].mean()), 3) if valid.any() else 0.0,
        "permanent_water_km2": round(float(perm.sum()) * px_km2, 3),
        "stream_area_threshold_km2": cfg.stream_area_km2,
        "hand_max_m": cfg.hand_max_m,
        "slope_max_deg": cfg.slope_max_deg,
    }
    return StageResult(
        "terrain", StageStatus.PARTIAL if warnings else StageStatus.OK,
        f"plausible flood zone {stats['plausible_zone_km2']} km² "
        f"({stats['plausible_share']:.0%} of area), permanent water "
        f"{stats['permanent_water_km2']} km²",
        outputs={**stats, "radar_geometry": geometry,
                 "files": {"terrain": terrain_tif, "hydrology": hydro_tif, "png": [png]}},
        warnings=warnings,
    )


def _render(hand, slope, plausible, layers, perm, transform, out_png, event_name):
    ext = [transform.c / 1000, (transform.c + hand.shape[1] * transform.a) / 1000,
           (transform.f + hand.shape[0] * transform.e) / 1000, transform.f / 1000]
    geom_keys = [k for k in layers if k.startswith("geom_ok_")]
    fig, axes = plt.subplots(1, 3 + len(geom_keys), figsize=(5 * (3 + len(geom_keys)), 6),
                             constrained_layout=True)
    im = axes[0].imshow(np.clip(hand, 0, 200), cmap="viridis_r", extent=ext)
    fig.colorbar(im, ax=axes[0], shrink=0.7, label="m")
    axes[0].set_title("HAND: height above nearest stream")
    im = axes[1].imshow(slope, cmap="magma", vmin=0, vmax=60, extent=ext)
    fig.colorbar(im, ax=axes[1], shrink=0.7, label="degrees")
    axes[1].set_title("Slope (Copernicus DEM)")
    zone = np.where(perm, 2, plausible.astype(int))
    axes[2].imshow(zone, cmap=matplotlib.colors.ListedColormap(["#eeeeee", "#9ecae1",
                                                                "#08306b"]),
                   vmin=-0.5, vmax=2.5, extent=ext, interpolation="nearest")
    axes[2].set_title("Plausible flood zone (light)\npermanent water before event (dark)")
    for ax, k in zip(axes[3:], geom_keys, strict=True):
        ax.imshow(layers[k], cmap="gray", vmin=0, vmax=1, extent=ext, interpolation="nearest")
        ax.set_title(f"{k.replace('geom_ok_', '')}: black = layover/shadow")
    for ax in axes:
        ax.set_xlabel("Easting (km)", fontsize=8)
        ax.tick_params(labelsize=7)
    fig.suptitle(f"Terrain masks (Copernicus DEM GLO-30) — {event_name}", fontsize=13)
    fig.text(0.5, -0.03, attribution.footer_text(), ha="center", fontsize=6, wrap=True)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=90, bbox_inches="tight")
    plt.close(fig)
    return out_png
