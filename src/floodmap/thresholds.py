"""Turn radar change (log-ratio) into flood / debris classes.

Log-ratio LR = 10*log10(after / before) in dB, per pixel:
  LR < 0  backscatter dropped  -> surface became smoother: new water, wet mud
  LR > 0  backscatter rose     -> surface became rougher: boulders, debris, wreckage
  LR ~ 0  no change (or change below speckle noise)

Classes (uint8):
  0 NODATA      outside the image / invalid
  1 NO_CHANGE
  2 WATER       strong decrease (VV or VH) AND dark VV after the event (open water)
  3 DECREASE    strong decrease (VV or VH) but not water-dark (wet mud, wet debris)
  4 INCREASE    strong increase in VV or VH (rough debris, boulder deposits, wreckage)

Two threshold methods:
  fixed  ±3 dB (backscatter halved/doubled) — transparent and predictable on unseen areas.
  otsu   data-driven per tail of the histogram, accepted only if the tail is clearly
         two-peaked and the value is within sane bounds, otherwise falls back to fixed.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage

NODATA, NO_CHANGE, WATER, DECREASE, INCREASE = 0, 1, 2, 3, 4
CLASS_NAMES = {NODATA: "no data", NO_CHANGE: "no significant change", WATER: "new water-like",
               DECREASE: "strong decrease (wet mud / debris)",
               INCREASE: "strong increase (rough debris)"}


@dataclass(frozen=True)
class ThresholdParams:
    method: str
    decrease_db: float
    increase_db: float
    water_post_vv_db: float
    otsu_decrease_bounds_db: tuple[float, float]
    otsu_increase_bounds_db: tuple[float, float]
    otsu_min_separability: float
    min_mapping_unit_px: int


def otsu_threshold(values: np.ndarray, bins: int = 256) -> tuple[float, float]:
    """Otsu's threshold and its separability (between-class / total variance, 0..1).

    Separability near 1 = two well-separated groups; near 0 = one blob, threshold meaningless.
    """
    values = values[np.isfinite(values)]
    if values.size < 100 or np.ptp(values) == 0:
        return float("nan"), 0.0
    hist, edges = np.histogram(values, bins=bins)
    centers = (edges[:-1] + edges[1:]) / 2
    weights = hist.astype(float) / hist.sum()
    w0 = np.cumsum(weights)
    w1 = 1.0 - w0
    m = np.cumsum(weights * centers)
    mean_total = m[-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        between = (mean_total * w0 - m) ** 2 / (w0 * w1)
    between[~np.isfinite(between)] = 0
    idx = int(np.argmax(between))
    total_var = float(np.sum(weights * (centers - mean_total) ** 2))
    separability = float(between[idx] / total_var) if total_var > 0 else 0.0
    return float(centers[idx]), separability


def _tail_threshold(values: np.ndarray, fixed: float, bounds: tuple[float, float],
                    min_sep: float) -> tuple[float, str]:
    t, sep = otsu_threshold(values)
    lo, hi = bounds
    if np.isfinite(t) and sep >= min_sep and lo <= t <= hi:
        return t, f"otsu (separability {sep:.2f})"
    return fixed, f"fixed (otsu rejected: threshold {t:.2f} dB, separability {sep:.2f})"


def choose_thresholds(lr: np.ndarray, valid: np.ndarray, p: ThresholdParams) -> dict:
    """Decrease/increase thresholds (dB) and a note on how each was chosen."""
    if p.method == "fixed":
        return {"decrease_db": p.decrease_db, "increase_db": p.increase_db,
                "decrease_method": "fixed", "increase_method": "fixed"}
    vals = lr[valid & np.isfinite(lr)]
    dec, dec_m = _tail_threshold(vals[vals < 0], p.decrease_db, p.otsu_decrease_bounds_db,
                                 p.otsu_min_separability)
    inc, inc_m = _tail_threshold(vals[vals > 0], p.increase_db, p.otsu_increase_bounds_db,
                                 p.otsu_min_separability)
    return {"decrease_db": dec, "increase_db": inc, "decrease_method": dec_m,
            "increase_method": inc_m}


def remove_small_patches(mask: np.ndarray, min_px: int) -> np.ndarray:
    """Drop connected patches (8-connectivity) smaller than `min_px` pixels."""
    if min_px <= 1 or not mask.any():
        return mask
    labels, n = ndimage.label(mask, structure=np.ones((3, 3), dtype=bool))
    sizes = np.bincount(labels.ravel())
    keep = sizes >= min_px
    keep[0] = False
    return keep[labels]


def classify(
    post_vv_db: np.ndarray,
    lr_vv: np.ndarray,
    lr_vh: np.ndarray | None,
    valid: np.ndarray,
    p: ThresholdParams,
) -> tuple[np.ndarray, dict]:
    """Classify every pixel; returns (classes uint8, info about thresholds used)."""
    th = choose_thresholds(lr_vv, valid, p)
    dec_t, inc_t = th["decrease_db"], th["increase_db"]

    decrease = valid & (lr_vv <= dec_t)
    increase = valid & (lr_vv >= inc_t)
    if lr_vh is not None:
        vh_ok = valid & np.isfinite(lr_vh)
        # VH (cross-polarised) reacts strongly to volume scattering: vegetation, debris piles,
        # rough banks. In the M1 ablation it traced the flooded channel far more continuously
        # than VV, so a strong VH drop also counts as a decrease (owner decision P16).
        decrease |= vh_ok & (lr_vh <= dec_t)
        increase |= vh_ok & (lr_vh >= inc_t)
        increase &= ~decrease  # conflicting signals: the drop wins (conservative for water)
    water = decrease & (post_vv_db <= p.water_post_vv_db)
    wet = decrease & ~water

    classes = np.full(lr_vv.shape, NODATA, dtype=np.uint8)
    classes[valid] = NO_CHANGE
    for code, mask in ((WATER, water), (DECREASE, wet), (INCREASE, increase & ~decrease)):
        classes[remove_small_patches(mask, p.min_mapping_unit_px)] = code
    th["water_post_vv_db"] = p.water_post_vv_db
    th["min_mapping_unit_px"] = p.min_mapping_unit_px
    return classes, th


def class_areas_km2(classes: np.ndarray, pixel_area_m2: float,
                    inside: np.ndarray | None = None) -> dict[str, float]:
    """Area per class in km², optionally only for pixels inside the AOI polygon."""
    sel = classes if inside is None else classes[inside]
    counts = np.bincount(sel.ravel(), minlength=5)
    return {CLASS_NAMES[c]: round(float(counts[c]) * pixel_area_m2 / 1e6, 4)
            for c in (NO_CHANGE, WATER, DECREASE, INCREASE)}
