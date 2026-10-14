"""Helicopter landing-zone CANDIDATES near settlements that are likely cut off by road.

Extra from the author's original list (M8). Inputs are only allowed data: Copernicus DEM
slope, our own flood/debris map, pre-event OSM buildings. No OSM helipad tags are used.
A candidate is a spot where a clear square of `min_clear_square_m` fits on near-level
ground, outside detected flood/debris and water, away from mapped buildings. Trees, power
lines, ground firmness and approach paths cannot be seen in 20-30 m data, so every candidate
must be checked on site: the output never says "safe".
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree

# final flood-map classes (sampark.flood_map): 0 no data, 1 no change, 2-4 change,
# 5 permanent water, 6 outside plausible zone (high/steep ground the flood did not reach)
USABLE_CLASSES = (1, 6)


def suitable_mask(slope_deg: np.ndarray, final_class: np.ndarray, buildings: np.ndarray,
                  max_slope_deg: float) -> np.ndarray:
    """Pixels that are near-level, not flooded/water, observed, and not near buildings."""
    return (np.isfinite(slope_deg) & (slope_deg <= max_slope_deg)
            & np.isin(final_class, USABLE_CLASSES) & ~buildings)


def candidates(suitable: np.ndarray, res: float, min_square_m: float
               ) -> list[tuple[int, int, float]]:
    """(row, col, clear square side in m) per candidate patch.

    A pixel qualifies if a `min_square_m` square centred on it is entirely suitable
    (binary erosion). Each connected qualifying patch gives one candidate: its most interior
    pixel (largest distance to the patch edge), so the spot is as far from obstacles as the
    patch allows.
    """
    side = max(1, int(round(min_square_m / res)))
    core = ndimage.binary_erosion(suitable, structure=np.ones((side, side), bool),
                                  border_value=0)
    labels, n = ndimage.label(core)
    if n == 0:
        return []
    dist = ndimage.distance_transform_edt(suitable)
    best = ndimage.maximum_position(dist, labels, index=np.arange(1, n + 1))
    return [(int(r), int(c), round(float(2 * dist[r, c] * res), 0)) for r, c in best]


def nearest_for(settlement_xy: np.ndarray, cand_xy: np.ndarray, max_distance_m: float, k: int
                ) -> list[list[tuple[int, float]]]:
    """For each settlement: up to k (candidate index, distance m) within max_distance_m."""
    if len(cand_xy) == 0 or len(settlement_xy) == 0:
        return [[] for _ in range(len(settlement_xy))]
    d, i = cKDTree(cand_xy).query(settlement_xy, k=min(k, len(cand_xy)),
                                  distance_upper_bound=max_distance_m)
    d, i = np.atleast_2d(d), np.atleast_2d(i)
    if d.shape[0] != len(settlement_xy):
        d, i = d.T, i.T
    return [[(int(ii), float(dd)) for dd, ii in zip(row_d, row_i, strict=True)
             if np.isfinite(dd)] for row_d, row_i in zip(d, i, strict=True)]
