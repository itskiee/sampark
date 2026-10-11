"""Ablation study for the Sentinel-1 change step (evidence for "what if we removed X?").

    python scripts/ablation_s1.py --cache-dir cache/<aoi>_<date>/s1_download_<hash> \
        --window 313000 3083000 327000 3100000 --out outputs/ablation_s1

Uses already-downloaded per-image GeoTIFFs (post.tif, pre_1.tif, ...), so it costs no
CDSE requests. Variants (each changes ONE thing versus the baseline):
  baseline        3x3 speckle median, median of all pre images, VV, fixed ±3 dB
  no_speckle      speckle filter switched off
  single_pre      only the newest pre image instead of the median of several
  vh_only         VH log-ratio instead of VV
  otsu            data-driven (Otsu) thresholds instead of fixed ±3 dB
Metrics: spread of the log-ratio (noise level), share of pixels flagged as changed, and
the share of flagged pixels that are isolated (no flagged neighbour = likely noise).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import rasterio  # noqa: E402
from rasterio.windows import from_bounds  # noqa: E402
from scipy import ndimage  # noqa: E402

from floodmap import attribution  # noqa: E402
from floodmap.config import load_config  # noqa: E402
from floodmap.s1_change import change_layers, load_separate  # noqa: E402
from floodmap.thresholds import NO_CHANGE, NODATA, ThresholdParams, classify  # noqa: E402
from floodmap.viz import CLASS_CMAP  # noqa: E402


def _params(cfg, method: str) -> ThresholdParams:
    return ThresholdParams(method, cfg.decrease_db, cfg.increase_db, cfg.water_post_vv_db,
                           tuple(cfg.otsu_decrease_bounds_db), tuple(cfg.otsu_increase_bounds_db),
                           cfg.otsu_min_separability,
                           max(1, round(cfg.min_mapping_unit_m2 / cfg.resolution_m**2)))


def _crop(arr, win):
    return arr[win]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cache-dir", type=Path, required=True)
    ap.add_argument("--window", type=float, nargs=4, metavar=("MINX", "MINY", "MAXX", "MAXY"),
                    help="UTM window to analyse (default: whole image)")
    ap.add_argument("--out", type=Path, default=Path("outputs/ablation_s1"))
    args = ap.parse_args()

    cfg = load_config().s1
    post = args.cache_dir / "post.tif"
    pres = sorted(args.cache_dir.glob("pre_*.tif"))
    if not post.exists() or not pres:
        print("cache dir must contain post.tif and pre_*.tif (per-image downloads)")
        return 1

    with rasterio.open(post) as src:
        win = (from_bounds(*args.window, src.transform).round_offsets().round_lengths()
               if args.window else None)
        rows, cols = (slice(int(win.row_off), int(win.row_off + win.height)),
                      slice(int(win.col_off), int(win.col_off + win.width))) if win else (
            slice(None), slice(None))

    variants = {
        "baseline": dict(window=cfg.speckle_window_px, pres=pres, band="VV", method="fixed"),
        "no_speckle": dict(window=1, pres=pres, band="VV", method="fixed"),
        "single_pre": dict(window=cfg.speckle_window_px, pres=pres[:1], band="VV",
                           method="fixed"),
        "vh_only": dict(window=cfg.speckle_window_px, pres=pres, band="VH", method="fixed"),
        "otsu": dict(window=cfg.speckle_window_px, pres=pres, band="VV", method="otsu"),
    }
    args.out.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(2, len(variants), figsize=(4.2 * len(variants), 9),
                             constrained_layout=True)
    print(f"{'variant':<12} {'LR std dB':>9} {'flagged %':>9} {'isolated %':>10}  thresholds")
    for j, (name, v) in enumerate(variants.items()):
        pre_lin, post_lin, transform, crs = load_separate(post, v["pres"])
        layers = change_layers(pre_lin, post_lin, transform, crs, v["window"],
                               cfg.min_valid_db, len(v["pres"]))
        lr = layers.lr_vh if v["band"] == "VH" else layers.lr_vv
        classes, th = classify(layers.post_vv_db, lr, None, layers.valid,
                               _params(cfg, v["method"]))
        lr_c, cls_c = lr[rows, cols], classes[rows, cols]
        valid = np.isfinite(lr_c)
        flagged = (cls_c != NO_CHANGE) & (cls_c != NODATA)
        neighbours = ndimage.convolve(flagged.astype(int), np.ones((3, 3), int), mode="constant")
        isolated = flagged & (neighbours == 1)
        std = float(np.nanstd(lr_c))
        pct = 100 * float(flagged[valid].mean())
        iso = 100 * float(isolated.sum() / max(flagged.sum(), 1))
        th_txt = f"{th['decrease_db']:.1f}/{th['increase_db']:.1f} ({th['decrease_method'][:5]})"
        print(f"{name:<12} {std:>9.2f} {pct:>9.2f} {iso:>10.1f}  {th_txt}")
        axes[0, j].imshow(lr_c, cmap="RdBu_r", vmin=-8, vmax=8)
        axes[0, j].set_title(f"{name}\nlog-ratio std {std:.2f} dB", fontsize=10)
        axes[1, j].imshow(cls_c, cmap=CLASS_CMAP, vmin=-0.5, vmax=4.5, interpolation="nearest")
        axes[1, j].set_title(f"flagged {pct:.1f}%, isolated {iso:.0f}%", fontsize=10)
        for a in axes[:, j]:
            a.set_xticks([])
            a.set_yticks([])
    fig.suptitle("Sentinel-1 ablation (one change per column vs baseline)", fontsize=13)
    fig.text(0.5, -0.02, attribution.footer_text(), ha="center", fontsize=6, wrap=True)
    out_png = args.out / "ablation_s1.png"
    fig.savefig(out_png, dpi=90, bbox_inches="tight")
    print(f"saved {out_png}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
