"""Stage "segmentation": our own U-Net water model on the event's Sentinel-1 images.

Runs the trained model (floodmap/segmentation.py, onnxruntime, fully local) on the primary
track's BEFORE and AFTER images: water probability per pixel, water before/after, and NEW
water = water after but not before. Inside the terrain-plausible zone and where the radar
geometry is valid, exactly like the change detection.

Why the model is NOT counted as an extra source in the confidence map: it reads the same
Sentinel-1 image as the change detection, so their agreement is not independent evidence
and would inflate "high confidence". It is reported as its own layer, with its measured
agreement with the change detection (the comparison with official reference maps is done
outside the pipeline in M7, for checking only).
Outputs: segmentation/segmentation.tif, segmentation/segmentation.png.
"""

from __future__ import annotations

import textwrap

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import rasterio  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from rasterio.features import geometry_mask  # noqa: E402

from floodmap import attribution  # noqa: E402
from floodmap.flood_map import WATER  # noqa: E402
from floodmap.pipeline import StageContext, register_stage  # noqa: E402
from floodmap.results import StageResult, StageStatus  # noqa: E402
from floodmap.s1_change import write_geotiff  # noqa: E402
from floodmap.s1_pairing import to_utm  # noqa: E402
from floodmap.segmentation import WaterModel, available  # noqa: E402
from floodmap.thresholds import remove_small_patches  # noqa: E402


def _band(src, name):
    return src.read(list(src.descriptions).index(name) + 1).astype("float32")


def agreement(model: np.ndarray, reference: np.ndarray, inside: np.ndarray) -> dict:
    """How the model's new water compares with another map (pixel counts inside `inside`)."""
    m, r = model & inside, reference & inside
    both, m_only, r_only = int((m & r).sum()), int((m & ~r).sum()), int((~m & r).sum())
    union = both + m_only + r_only
    return {"iou": round(both / union, 3) if union else None,
            "share_of_model_also_in_reference": round(both / (both + m_only), 3)
            if both + m_only else None,
            "share_of_reference_also_in_model": round(both / (both + r_only), 3)
            if both + r_only else None,
            "pixels_both": both, "pixels_model_only": m_only, "pixels_reference_only": r_only}


@register_stage("segmentation", requires=("s1",), uses=("terrain", "confidence"))
def run_segmentation(ctx: StageContext) -> StageResult:
    if not available():
        return StageResult("segmentation", StageStatus.SKIPPED,
                           "Model file models/segmentation/s1_water_unet.onnx is missing "
                           "(train it with scripts/train_segmentation.py).")
    s1 = ctx.results.stages["s1"].outputs
    track = s1["tracks"][0]
    with rasterio.open(track["files"]["change"]) as src:
        pre_vv, post_vv = _band(src, "pre_vv_db"), _band(src, "post_vv_db")
        pre_vh, lr_vh = _band(src, "pre_vh_db"), _band(src, "logratio_vh_db")
        transform, crs, shape = src.transform, src.crs, src.shape
    post_vh = pre_vh + lr_vh
    warnings: list[str] = []
    if not np.isfinite(pre_vh).any():
        return StageResult("segmentation", StageStatus.SKIPPED,
                           "No VH polarisation for this track: the model needs VV + VH.")

    model = WaterModel()
    thr = float(model.card.get("threshold", 0.5))
    prob_pre = model.probability(pre_vv, pre_vh)
    prob_post = model.probability(post_vv, post_vh)
    valid = np.isfinite(prob_pre) & np.isfinite(prob_post)
    water_pre = valid & (np.nan_to_num(prob_pre) >= thr)
    water_post = valid & (np.nan_to_num(prob_post) >= thr)
    new_water = water_post & ~water_pre

    res = abs(transform.a)
    inside = ~geometry_mask([to_utm(ctx.aoi.geometry, ctx.aoi.utm_epsg)], out_shape=shape,
                            transform=transform)
    zone = inside.copy()
    terr = ctx.results.stages.get("terrain")
    if terr is not None and terr.usable:
        from floodmap.stages.confidence import _read_band_by_name

        tpath = terr.outputs["files"]["terrain"]
        tag = f"track{track['pair']['relative_orbit']}_{track['pair']['orbit_state']}"
        zone &= _read_band_by_name(tpath, "plausible", shape, transform, crs, True) > 0.5
        zone &= _read_band_by_name(tpath, f"geom_ok_{tag}", shape, transform, crs, True) > 0.5
    else:
        warnings.append("terrain masks unavailable: new water not limited to the plausible zone")
    min_px = max(1, round(ctx.config.s1.min_mapping_unit_m2 / res**2))
    new_water = remove_small_patches(new_water & zone, min_px)

    km2 = res**2 / 1e6
    outputs = {
        "model": model.card.get("name", "U-Net"),
        "model_test_scores": {k: model.card.get("evaluation", {}).get(k, {}).get("model")
                              for k in ("test", "bolivia")},
        "trained_on": model.card.get("training_data"),
        "citation": model.card.get("citation"),
        "track": track["pair"]["track"],
        "threshold": thr,
        # inside the terrain-plausible zone with valid radar geometry (same as the change
        # detection); outside it the model mostly sees radar shadow on steep slopes
        "water_before_km2": round(float((water_pre & zone).sum() * km2), 3),
        "water_after_km2": round(float((water_post & zone).sum() * km2), 3),
        "new_water_km2": round(float(new_water.sum() * km2), 3),
        "water_after_outside_plausible_zone_km2": round(float((water_post & inside & ~zone)
                                                              .sum() * km2), 3),
        "domain_shift_note": "trained on lowland floods: in steep terrain the model mistakes "
                             "dark radar shadow for water; such areas are outside the plausible "
                             "zone and are not counted",
        "note": "same Sentinel-1 image as the change detection, so not counted as an "
                "independent source in the confidence map",
    }
    conf = ctx.results.stages.get("confidence")
    if conf is not None and conf.usable:
        with rasterio.open(conf.outputs["files"]["flood_map"]) as src:
            final = src.read(1)
        outputs["agreement_with_change_detection_water"] = agreement(new_water, final == WATER,
                                                                     zone)

    out = ctx.results.run_dir / "segmentation"
    tif = write_geotiff(out / "segmentation.tif", {
        "water_prob_before": prob_pre, "water_prob_after": prob_post,
        "new_water": new_water.astype("float32")}, transform, crs)
    png = _render(ctx, post_vv, prob_post, new_water,
                  (final == WATER) & zone if conf is not None and conf.usable else None,
                  transform, outputs, out / "segmentation.png")
    outputs["files"] = {"raster": tif, "png": [png]}
    msg = (f"own U-Net: new water {outputs['new_water_km2']:.2f} km² in the plausible zone "
           f"(water before {outputs['water_before_km2']:.2f}, after "
           f"{outputs['water_after_km2']:.2f} km²; "
           f"{outputs['water_after_outside_plausible_zone_km2']:.2f} km² 'water' outside it, "
           "mostly radar shadow)")
    agr = outputs.get("agreement_with_change_detection_water")
    if agr and agr["iou"] is not None:
        msg += f"; agreement with change-detection water: IoU {agr['iou']:.2f}"
    return StageResult("segmentation", StageStatus.PARTIAL if warnings else StageStatus.OK,
                       msg, outputs=outputs, warnings=warnings)


def _render(ctx, post_vv, prob_post, new_water, cd_water, transform, outputs, out_png):
    from floodmap.viz import _extent_km, _scale_bar, crop_window

    win = None
    if ctx.focus_bbox:
        win = crop_window(transform, post_vv.shape, ctx.focus_bbox, ctx.aoi.utm_epsg)
    rows, cols = win or (slice(0, post_vv.shape[0]), slice(0, post_vv.shape[1]))
    ext = _extent_km(transform, rows, cols)
    fig, axes = plt.subplots(1, 3, figsize=(16, 7), constrained_layout=True)
    axes[0].imshow(post_vv[rows, cols], cmap="gray", vmin=-25, vmax=0, extent=ext)
    axes[0].set_title("After the event: Sentinel-1 VV (dB)")
    im = axes[1].imshow(prob_post[rows, cols], cmap="Blues", vmin=0, vmax=1, extent=ext)
    fig.colorbar(im, ax=axes[1], shrink=0.7)
    axes[1].set_title("Our U-Net: water probability (after)")
    rgb = np.ones(new_water[rows, cols].shape + (3,))
    nw = new_water[rows, cols]
    if cd_water is not None:
        cw = cd_water[rows, cols]
        rgb[nw & cw] = (0.42, 0.24, 0.6)
        rgb[nw & ~cw] = (0.12, 0.39, 0.78)
        rgb[~nw & cw] = (0.9, 0.62, 0.0)
        handles = [Patch(color=(0.42, 0.24, 0.6), label="both"),
                   Patch(color=(0.12, 0.39, 0.78), label="model only"),
                   Patch(color=(0.9, 0.62, 0.0), label="change detection only")]
    else:
        rgb[nw] = (0.12, 0.39, 0.78)
        handles = [Patch(color=(0.12, 0.39, 0.78), label="model new water")]
    axes[2].imshow(rgb, extent=ext)
    axes[2].legend(handles=handles, loc="lower right", fontsize=8)
    axes[2].set_title("New water: model vs change detection")
    for ax in axes:
        ax.set_xlabel("Easting (km)", fontsize=8)
        _scale_bar(ax, ext)
    scores = outputs["model_test_scores"]
    sub = (f"Model trained by us from scratch on Sen1Floods11 (no Himalayan scenes). "
           f"Test IoU {scores['test']['iou'] if scores.get('test') else 'n/a'}, "
           f"unseen Bolivia event IoU "
           f"{scores['bolivia']['iou'] if scores.get('bolivia') else 'n/a'}. "
           "Estimate only; verify on the ground.")
    fig.suptitle(f"Own flood segmentation model (AI component) — event {ctx.event_date} — "
                 f"{ctx.results.event_name}\n{sub}", fontsize=11)
    footer = "\n".join(textwrap.wrap(attribution.footer_text() + " Training data: " +
                                     attribution.SEN1FLOODS11_CITATION, 190))
    fig.text(0.5, -0.02, footer, ha="center", va="top", fontsize=7, color="#444444")
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=110, bbox_inches="tight")
    plt.close(fig)
    return out_png
