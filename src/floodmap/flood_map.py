"""Combine independent detections into the final flood/debris map with confidence.

Sources (each with its own validity): Sentinel-1 primary track, Sentinel-1 track from the
opposite pass direction, and (when cloud-free) Sentinel-2. A detection only counts inside
the physically plausible zone (low HAND, not too steep) and where that radar track has no
layover/shadow.

Final classes (uint8):
  0 NODATA           no valid observation
  1 NO_CHANGE
  2 WATER            new water-like
  3 DECREASE         wet mud / debris (backscatter drop, not water-dark)
  4 INCREASE         rough debris (backscatter rise)
  5 PERMANENT_WATER  already water before the event (normal river), not reported as flood
  6 MASKED           outside the plausible zone (high above the river or too steep)
Confidence (uint8): 0 none, 1 low, 2 medium, 3 high
  high    at least two independent sources agree (within a small neighbourhood)
  medium  one source, but a coherent patch of at least `medium_min_patch_px` pixels
  low     one source, small patch
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from floodmap.thresholds import DECREASE, INCREASE, NO_CHANGE, NODATA, WATER

PERMANENT_WATER, MASKED = 5, 6
FINAL_CLASS_NAMES = {NODATA: "no data", NO_CHANGE: "no significant change",
                     WATER: "new water-like",
                     # radar measures darker/brighter, not the material: mud/debris is the
                     # LIKELY cause (crops, soil moisture or construction can do the same)
                     DECREASE: "backscatter decrease (likely wet mud / debris)",
                     INCREASE: "backscatter increase (likely rough debris)",
                     PERMANENT_WATER: "permanent water",
                     MASKED: "outside plausible zone"}
CONF_NONE, CONF_LOW, CONF_MEDIUM, CONF_HIGH = 0, 1, 2, 3
CONF_NAMES = {CONF_LOW: "low", CONF_MEDIUM: "medium", CONF_HIGH: "high"}
CHANGE_CLASSES = (WATER, DECREASE, INCREASE)


@dataclass
class Source:
    name: str
    classes: np.ndarray  # per-source classes (thresholds.py codes), on the common grid
    usable: np.ndarray  # bool: valid observation with good geometry for this source


def combine(sources: list[Source], plausible: np.ndarray, permanent_water: np.ndarray,
            neighbourhood_px: int, medium_min_patch_px: int) -> tuple[np.ndarray, np.ndarray,
                                                                       np.ndarray]:
    """Return (final classes, confidence, number of agreeing sources)."""
    shape = plausible.shape
    any_usable = np.zeros(shape, bool)
    flags, dilated = [], []
    struct = np.ones((2 * neighbourhood_px + 1,) * 2, bool) if neighbourhood_px > 0 else None
    for s in sources:
        flag = s.usable & np.isin(s.classes, CHANGE_CLASSES) & plausible & ~permanent_water
        flags.append(flag)
        dilated.append(ndimage.binary_dilation(flag, structure=struct) if struct is not None
                       else flag)
        any_usable |= s.usable

    final = np.full(shape, NODATA, dtype=np.uint8)
    final[any_usable] = NO_CHANGE
    final[any_usable & ~plausible] = MASKED
    # class priority: water > decrease > increase (the more hazardous reading wins)
    for code in (INCREASE, DECREASE, WATER):
        for s, flag in zip(sources, flags, strict=True):
            final[flag & (s.classes == code)] = code
    final[permanent_water & any_usable] = PERMANENT_WATER

    flagged = np.isin(final, CHANGE_CLASSES)
    agree = np.zeros(shape, dtype=np.uint8)
    for d in dilated:
        agree += d.astype(np.uint8)
    agree[~flagged] = 0

    conf = np.zeros(shape, dtype=np.uint8)
    labels, n = ndimage.label(flagged, structure=np.ones((3, 3), bool))
    if n:
        sizes = np.bincount(labels.ravel())
        big = (sizes >= medium_min_patch_px)[labels] & flagged
        conf[flagged] = CONF_LOW
        conf[big] = CONF_MEDIUM
    conf[flagged & (agree >= 2)] = CONF_HIGH
    return final, conf, agree


def area_table(final: np.ndarray, conf: np.ndarray, pixel_area_m2: float,
               inside: np.ndarray | None = None) -> dict:
    """km² per final class, and per confidence level for the change classes."""
    sel = np.ones(final.shape, bool) if inside is None else inside
    km2 = pixel_area_m2 / 1e6
    by_class = {FINAL_CLASS_NAMES[c]: round(float(((final == c) & sel).sum()) * km2, 3)
                for c in (WATER, DECREASE, INCREASE, PERMANENT_WATER, NO_CHANGE, MASKED)}
    changed = np.isin(final, CHANGE_CLASSES) & sel
    by_conf = {CONF_NAMES[c]: round(float((changed & (conf == c)).sum()) * km2, 3)
               for c in (CONF_HIGH, CONF_MEDIUM, CONF_LOW)}
    return {"by_class_km2": by_class, "affected_by_confidence_km2": by_conf,
            "affected_total_km2": round(float(changed.sum()) * km2, 3)}
