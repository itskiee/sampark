"""Quick-look map figures (PNG). Every figure carries the required attributions and the
educational-prototype disclaimer (floodmap.attribution)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # no screen needed (servers, CI)
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import ListedColormap  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from shapely.geometry import box  # noqa: E402

from floodmap import attribution  # noqa: E402
from floodmap.s1_pairing import to_utm  # noqa: E402
from floodmap.thresholds import CLASS_NAMES, DECREASE, INCREASE, NO_CHANGE, NODATA, WATER  # noqa: E402

# Colour-blind-friendly class colours: blue = water, orange = wet mud, magenta = debris.
CLASS_COLORS = {NODATA: "#ffffff", NO_CHANGE: "#d9d9d9", WATER: "#1f63c6",
                DECREASE: "#e69f00", INCREASE: "#c51b7d"}
CLASS_CMAP = ListedColormap([CLASS_COLORS[i] for i in range(5)])


def crop_window(transform, shape, bbox_wgs84, epsg) -> tuple[slice, slice] | None:
    """Row/column slices of the raster covering a lon/lat bbox (None if outside)."""
    minx, miny, maxx, maxy = to_utm(box(*bbox_wgs84), epsg).bounds
    inv = ~transform
    c0, r0 = inv @ (minx, maxy)
    c1, r1 = inv @ (maxx, miny)
    r0, r1 = sorted((int(np.floor(r0)), int(np.ceil(r1))))
    c0, c1 = sorted((int(np.floor(c0)), int(np.ceil(c1))))
    r0, c0 = max(r0, 0), max(c0, 0)
    r1, c1 = min(r1, shape[0]), min(c1, shape[1])
    if r1 - r0 < 2 or c1 - c0 < 2:
        return None
    return slice(r0, r1), slice(c0, c1)


def _extent_km(transform, rows: slice, cols: slice) -> list[float]:
    x0 = transform.c + cols.start * transform.a
    x1 = transform.c + cols.stop * transform.a
    y0 = transform.f + rows.stop * transform.e
    y1 = transform.f + rows.start * transform.e
    return [x0 / 1000, x1 / 1000, y0 / 1000, y1 / 1000]


def _scale_bar(ax, extent: list[float]) -> None:
    width = extent[1] - extent[0]
    candidates = (50, 20, 10, 5, 2, 1, 0.5, 0.2, 0.1)
    length = next((v for v in candidates if v <= width / 4), width / 4)  # tiny maps too
    x = extent[0] + width * 0.05
    y = extent[2] + (extent[3] - extent[2]) * 0.05
    ax.plot([x, x + length], [y, y], color="black", lw=3, solid_capstyle="butt")
    ax.plot([x, x + length], [y, y], color="white", lw=1.2, solid_capstyle="butt")
    ax.text(x + length / 2, y, f"{length:g} km\n", ha="center", va="bottom", fontsize=7,
            color="black", bbox={"facecolor": "white", "alpha": 0.6, "lw": 0, "pad": 1})


def _rgb_composite(pre_db: np.ndarray, post_db: np.ndarray) -> np.ndarray:
    """R = before, G = B = after. Red = got darker (water/mud), cyan = got brighter (debris)."""
    def scale(a):
        return np.clip((a + 25.0) / 25.0, 0, 1)

    rgb = np.dstack([scale(pre_db), scale(post_db), scale(post_db)])
    rgb[~np.isfinite(rgb)] = 1.0
    return rgb


def render_s1(pre_db, post_db, lr_db, classes, transform, title: str, subtitle: str,
              out_png: Path, window: tuple[slice, slice] | None = None) -> Path:
    rows, cols = window or (slice(0, classes.shape[0]), slice(0, classes.shape[1]))
    ext = _extent_km(transform, rows, cols)
    pre, post, lr, cls = (a[rows, cols] for a in (pre_db, post_db, lr_db, classes))

    fig, axes = plt.subplots(2, 3, figsize=(15, 10.5), constrained_layout=True)
    panels = [
        (axes[0, 0], pre, {"cmap": "gray", "vmin": -25, "vmax": 0},
         "Before (VV backscatter, dB)\nmedian of pre-event images"),
        (axes[0, 1], post, {"cmap": "gray", "vmin": -25, "vmax": 0},
         "After (VV backscatter, dB)"),
        (axes[0, 2], lr, {"cmap": "RdBu_r", "vmin": -8, "vmax": 8},
         "Change: log-ratio after/before (dB)\nblue = darker, red = brighter"),
    ]
    for ax, data, kw, label in panels:
        im = ax.imshow(data, extent=ext, interpolation="nearest", **kw)
        fig.colorbar(im, ax=ax, shrink=0.75)
        ax.set_title(label, fontsize=10)
    axes[1, 0].imshow(_rgb_composite(pre, post), extent=ext, interpolation="nearest")
    axes[1, 0].set_title("RGB change composite\nR = before, G/B = after", fontsize=10)
    axes[1, 1].imshow(cls, cmap=CLASS_CMAP, vmin=-0.5, vmax=4.5, extent=ext,
                      interpolation="nearest")
    axes[1, 1].set_title("Change classes (Sentinel-1 only, no terrain masks yet)", fontsize=10)
    axes[1, 1].legend(handles=[Patch(color=CLASS_COLORS[c], label=CLASS_NAMES[c])
                               for c in (NO_CHANGE, WATER, DECREASE, INCREASE)],
                      loc="lower right", fontsize=7, framealpha=0.85)
    for ax in axes.flat[:5]:
        ax.set_xlabel("Easting (km)", fontsize=8)
        ax.set_ylabel("Northing (km)", fontsize=8)
        ax.tick_params(labelsize=7)
        _scale_bar(ax, ext)

    info = axes[1, 2]
    info.axis("off")
    info.text(0, 1, subtitle, va="top", ha="left", fontsize=9, family="monospace", wrap=True)

    fig.suptitle(title, fontsize=13, fontweight="bold")
    footer = "\n".join(textwrap.wrap(attribution.footer_text(), 190))
    fig.text(0.5, -0.01, footer, ha="center", va="top", fontsize=7, color="#444444")
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=110, bbox_inches="tight")
    plt.close(fig)
    return out_png
