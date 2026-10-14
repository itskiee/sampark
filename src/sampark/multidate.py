"""Multi-date tracking (M8 extra from the author's list): what happens after the first image.

Later Sentinel-1 passes on the SAME orbit track are compared with the SAME pre-event
composite and classified with the SAME frozen thresholds as the first post-event image.
From the sequence:
  receding water      water on the first post-event date, gone on the last
  new ponding         water on the last date that was not water on the first
  growing water body  a connected water body on the last date that grew strongly since the
                      first date: flagged as a POSSIBLE lake forming (e.g. behind debris),
                      a known secondary hazard after debris floods. Never confirmed: verify.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

from sampark.thresholds import DECREASE, INCREASE, WATER

# A water body counts as present on a later date when at least this share of its final
# area is water there (a single stray pixel is not "present").
PRESENT_SHARE = 0.10


def series_areas(class_maps: list[np.ndarray], zone: np.ndarray, pixel_km2: float
                 ) -> list[dict[str, float]]:
    """Per date: km² of water-like, wet mud/debris and rough debris inside `zone`."""
    out = []
    for cm in class_maps:
        out.append({"water_km2": round(float(((cm == WATER) & zone).sum() * pixel_km2), 3),
                    "wet_mud_debris_km2": round(float(((cm == DECREASE) & zone).sum()
                                                      * pixel_km2), 3),
                    "rough_debris_km2": round(float(((cm == INCREASE) & zone).sum()
                                                    * pixel_km2), 3),
                    "observed_share": round(float((cm[zone] > 0).mean()), 3)
                    if zone.any() else 0.0})
    return out


def water_changes(first: np.ndarray, last: np.ndarray, zone: np.ndarray, pixel_km2: float
                  ) -> tuple[dict[str, float], np.ndarray]:
    """Receding / new / persistent water between the first and last date (both observed)."""
    seen = (first > 0) & (last > 0) & zone
    w1, w2 = (first == WATER) & seen, (last == WATER) & seen
    code = np.zeros(first.shape, dtype=np.uint8)  # 1 receding, 2 new ponding, 3 persistent
    code[w1 & ~w2], code[~w1 & w2], code[w1 & w2] = 1, 2, 3
    km2 = {name: round(float((code == c).sum() * pixel_km2), 3)
           for c, name in ((1, "receding_water_km2"), (2, "new_ponding_km2"),
                           (3, "persistent_water_km2"))}
    return km2, code


def growing_water_bodies(class_maps: list[np.ndarray], zone: np.ndarray, pixel_m2: float,
                         min_area_m2: float, growth_factor: float,
                         min_later_dates: int = 2) -> list[dict]:
    """Water bodies on the last date that are at least `min_area_m2`, at least
    `growth_factor` times larger than the water in the same footprint on the first date, and
    present (>= PRESENT_SHARE of its final area) on at least `min_later_dates` later dates.

    The persistence rule removes water that appears on a single image only: wet snow and
    noise look as dark as water to radar (seen on the Trishuli run, 2026-10-13)."""
    last, first = class_maps[-1], class_maps[0]
    labels, n = ndimage.label((last == WATER) & zone)
    if n == 0:
        return []
    idx = np.arange(1, n + 1)
    area_last = ndimage.sum(np.ones_like(labels), labels, idx) * pixel_m2
    area_first = ndimage.sum((first == WATER).astype(float), labels, idx) * pixel_m2
    flags = []
    for lab, a2, a1 in zip(idx, area_last, area_first, strict=True):
        if a2 >= min_area_m2 and a2 >= growth_factor * max(a1, pixel_m2):
            rows, cols = np.nonzero(labels == lab)
            per_date = [round(float(((cm == WATER) & (labels == lab)).sum() * pixel_m2 / 1e6),
                              4) for cm in class_maps]
            present = [a >= PRESENT_SHARE * per_date[-1] for a in per_date[1:]]
            if sum(present) < min_later_dates:
                continue
            flags.append({"label": int(lab), "row": int(rows.mean()), "col": int(cols.mean()),
                          "area_km2_by_date": per_date,
                          "growth_factor": round(a2 / max(a1, pixel_m2), 1)})
    return sorted(flags, key=lambda f: -f["area_km2_by_date"][-1])
