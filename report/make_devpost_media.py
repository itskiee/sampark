"""Devpost thumbnail + image gallery (3:2, PNG, < 5 MB each) from our own outputs.

Every image is a map or page the system produced (each already carries the attribution
footer and the prototype label); nothing else, no photos (responsibility note).
Images are padded on white to 3:2, never cropped, so no footer text is lost.

Usage: .venv/Scripts/python -I report/make_devpost_media.py outputs/<case-study run id>
Writes report/devpost/thumbnail.png and report/devpost/gallery_*.png
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.image as mpimg  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "report" / "devpost"
NAVY, RED = "#16324f", "#b2182b"


def padded(src: Path, dst: Path, title: str | None = None, width_px: int = 1800) -> None:
    img = mpimg.imread(src)
    h, w = img.shape[:2]
    fig_w, fig_h = width_px / 100, width_px / 150  # 3:2
    fig = plt.figure(figsize=(fig_w, fig_h), dpi=100)
    fig.patch.set_facecolor("white")
    top = 0.88 if title else 0.98
    box_w, box_h = 0.96, top - 0.02
    scale = min(box_w * fig_w / w, box_h * fig_h / h)  # inches per pixel, keep aspect
    aw, ah = w * scale / fig_w, h * scale / fig_h
    ax = fig.add_axes([(1 - aw) / 2, 0.02 + (box_h - ah) / 2, aw, ah])
    ax.imshow(img)
    ax.axis("off")
    if title:
        fig.text(0.5, 0.955, title, ha="center", va="center", fontsize=26, color=NAVY,
                 fontweight="bold")
    fig.savefig(dst, dpi=100)
    plt.close(fig)


def thumbnail(run: Path, dst: Path) -> None:
    """Readable at Devpost's small card size: a dark panel with name, tagline and the key
    result (read from results.json, never typed), the cut-off map panel (cropped from our own
    map, no side text, no axis ticks) on the right, every attribution on a strip at the bottom."""
    import json

    from matplotlib.patches import Rectangle

    from floodmap import attribution

    res = json.loads((run / "results.json").read_text(encoding="utf-8"))
    cut = res["stages"]["cutoff"]["outputs"]["by_network"]["motorable"]
    img = mpimg.imread(run / "cutoff" / "cutoff_map_focus.png")
    h, w = img.shape[:2]
    panel = img[int(0.045 * h):int(0.78 * h), int(0.162 * w):int(0.481 * w)]  # inside the axes
    fig = plt.figure(figsize=(15, 10), dpi=100)  # 3:2
    fig.patch.set_facecolor("white")
    fig.add_artist(Rectangle((0, 0.075), 0.47, 0.925, transform=fig.transFigure,
                             color=NAVY, zorder=0))
    fig.add_artist(Rectangle((0, 0.075), 0.012, 0.925, transform=fig.transFigure,
                             color="#e8a33d", zorder=1))  # thin accent bar
    ph, pw = panel.shape[:2]  # keep the map's true proportions (never stretch a map)
    height = 0.84
    width = height * 10 * pw / ph / 15
    ax = fig.add_axes([0.47 + (0.53 - width) / 2, 0.135, width, height])
    ax.imshow(panel)
    ax.axis("off")
    fig.text(0.735, 0.105, "Red dots: settlements likely cut off by road    "
             "Red crosses: likely road or bridge breaks", ha="center", va="center",
             fontsize=12, color="#555555")

    # one type scale, one left edge, sections separated by thin rules
    x, x2, right = 0.06, 0.25, 0.43
    white, muted, accent = "white", "#aebfd1", "#ffb35c"
    title, sub, label, number, body, note = 64, 22, 13, 40, 17, 14

    def rule(y):
        fig.add_artist(plt.Line2D([x, right], [y, y], transform=fig.transFigure,
                                  color="#3b5672", linewidth=1.2))

    fig.text(x, 0.93, "Sampark", fontsize=title, fontweight="bold", color=white, va="top")
    fig.text(x, 0.815, "Who is cut off after a flood,\nmapped from space", fontsize=sub,
             color=muted, va="top", linespacing=1.3)
    rule(0.70)
    fig.text(x, 0.665, "CASE STUDY  ·  TRISHULI, NEPAL  ·  AUGUST 2026",
             fontsize=label, fontweight="bold", color=muted, va="top")
    for col, value, text in (
            (x, f"{cut['likely cut off']:,}", "settlements likely\ncut off by road"),
            (x2, f"{cut['buildings_in_cut_off_settlements']:,}", "buildings in those\n"
                                                                  "settlements")):
        fig.text(col, 0.615, value, fontsize=number, fontweight="bold", color=accent, va="top")
        fig.text(col, 0.535, text, fontsize=body, color=white, va="top", linespacing=1.35)
    rule(0.43)
    fig.text(x, 0.395, "BUILT WITH", fontsize=label, fontweight="bold", color=muted, va="top")
    fig.text(x, 0.35, "Sentinel-1 radar\nCopernicus DEM\nPre-event OpenStreetMap\n"
             "Our own AI flood model", fontsize=body, color=white, va="top", linespacing=1.45)
    fig.text(x, 0.125, "Educational prototype, not an operational tool", fontsize=note,
             color="#ff9c9c", va="center")
    # every required data attribution, word for word (attribution module, never retyped)
    fig.text(0.5, 0.008, "\n".join(textwrap.wrap(" ".join(attribution.DATA_ATTRIBUTIONS), 190)),
             ha="center", va="bottom", fontsize=9, color="#555555")
    fig.savefig(dst, dpi=100)
    plt.close(fig)


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    run = ROOT / sys.argv[1]
    OUT.mkdir(parents=True, exist_ok=True)
    thumbnail(run, OUT / "thumbnail.png")
    gallery = [
        (run / "cutoff" / "cutoff_map_focus.png", "Who is cut off? (Trishuli case study)"),
        (run / "confidence" / "flood_map_focus.png", "Where did the flood hit? With confidence"),
        (run / "damage" / "damage_map_focus.png", "What was damaged? (pre-event OpenStreetMap)"),
        (run / "report" / "sitrep.png", "One-page situation report, generated by the system"),
        (ROOT / "validation" / "results" / "EMSR927_AOI03_GRA_PRODUCT_v1.png",
         "Checked against Copernicus EMS (EMSR927), checking only"),
        (run / "segmentation" / "segmentation.png", "Our own flood segmentation model (U-Net)"),
        (run / "landing_zones" / "landing_zones.png", "Helicopter landing-zone candidates"),
        (run / "multidate" / "multidate.png", "Later satellite passes: possible lakes forming"),
        (run / "flood_path" / "flood_path.png", "Bonus: flood path down the valley"),
    ]
    for i, (src, title) in enumerate(gallery, start=1):
        if src.is_file():
            padded(src, OUT / f"gallery_{i:02d}.png", title)
    for p in sorted(OUT.glob("*.png")):
        print(f"{p.relative_to(ROOT)}  {p.stat().st_size / 1e6:.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
