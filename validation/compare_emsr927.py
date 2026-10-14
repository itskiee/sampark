"""Compare our results with the official Copernicus EMS maps for EMSR927 (checking only).

Brief: "Map the August 2026 Trishuli flood and compare your results with the official
Copernicus Emergency Management Service maps for this event (activation EMSR927)."
EMSR927 is NOT an input: this script only reads our finished run outputs and the reference
packages, and writes metrics. Our method was frozen before the reference data were opened
(configs/default.yaml unchanged since commit 281fd85, SHA-256 prefix f6062fb0f6df0693).

Usage:
  python -I validation/compare_emsr927.py outputs/<case study run> [outputs/<extra run> ...]
Output: validation/results/metrics.json, validation/results/<AOI>.png (results.md is written
from these by hand-checked text, every number copied from metrics.json).
Credit: European Union, Copernicus Emergency Management Service data.
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import rasterio  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from rasterio.features import rasterize  # noqa: E402
from shapely.geometry import box  # noqa: E402

from sampark import attribution  # noqa: E402

HERE = Path(__file__).resolve().parent
EMS = HERE / "data" / "EMSR927" / "extracted" / "EMSR927_products"
OUT = HERE / "results"
CHANGE = (2, 3, 4)  # our classes: new water-like, backscatter decrease, increase
DAMAGED = ("Destroyed", "Damaged")
NEAR_BUILDING_M = 15  # tolerance OSM footprint vs EMS point (different digitisations)
NEAR_ROAD_M = 20


def layer(prod: Path, name: str) -> gpd.GeoDataFrame | None:
    files = sorted(prod.glob(f"*_{name}_*.shp"))
    return gpd.read_file(files[0]) if files else None


def scores(ours: np.ndarray, ref: np.ndarray) -> dict:
    tp, fp, fn = int((ours & ref).sum()), int((ours & ~ref).sum()), int((~ours & ref).sum())

    def r(a, b):
        return round(a / b, 3) if b else None

    p, rec = r(tp, tp + fp), r(tp, tp + fn)
    return {"iou": r(tp, tp + fp + fn), "precision": p, "recall": rec,
            "f1": round(2 * p * rec / (p + rec), 3) if p and rec else None,
            "ours_km2": round((tp + fp) * PIX_KM2, 3),
            "reference_km2": round((tp + fn) * PIX_KM2, 3)}


PIX_KM2 = 0.0


def run_layers(run: Path):
    res = json.loads((run / "results.json").read_text(encoding="utf-8"))
    st = res["stages"]
    with rasterio.open(run / st["confidence"]["outputs"]["files"]["flood_map"]) as src:
        final, conf = src.read(1), src.read(2)
        transform, crs, shape = src.transform, src.crs, src.shape
    seg = None
    if st.get("segmentation", {}).get("status") in ("ok", "partial"):
        with rasterio.open(run / st["segmentation"]["outputs"]["files"]["raster"]) as src:
            seg = src.read(3) > 0.5
    dmg = st["damage"]["outputs"]["files"]
    layers = {
        "buildings": gpd.read_file(run / dmg["buildings"]).to_crs(crs),
        "roads": gpd.read_file(run / dmg["roads"]).to_crs(crs),
        "bridges": gpd.read_file(run / dmg["bridges"]).to_crs(crs),
        "polygons": gpd.read_file(run / st["confidence"]["outputs"]["files"]["polygons"]),
        "breaks": gpd.read_file(run / st["cutoff"]["outputs"]["files"]["breaks"])
        if st.get("cutoff", {}).get("status") in ("ok", "partial") else None,
    }
    hydro = run / st["terrain"]["outputs"]["files"]["hydrology"]
    return res, final, conf, seg, transform, crs, shape, layers, hydro


def compare(prod: Path, run: Path) -> dict | None:
    global PIX_KM2
    res, final, conf, seg, transform, crs, shape, ours, hydro = run_layers(run)
    PIX_KM2 = abs(transform.a * transform.e) / 1e6
    aoi = layer(prod, "areaOfInterestA").to_crs(crs)
    na = layer(prod, "notAnalysedA")
    region = aoi.geometry.union_all()
    if na is not None and not na.empty:
        region = region.difference(na.to_crs(crs).geometry.union_all())
    analysed = box(*gpd.GeoSeries([box(*res["aoi"]["bbox"])], crs=4326).to_crs(crs).total_bounds)
    covered = region.intersection(analysed)
    coverage = covered.area / region.area if region.area else 0
    if coverage < 0.2:
        return {"product": prod.name, "coverage_of_reference_area": round(coverage, 3),
                "note": "less than 20 % of this reference area lies inside the run's area"}
    inside = rasterize([covered], out_shape=shape, transform=transform).astype(bool)
    event = layer(prod, "observedEventA").to_crs(crs)
    ref = rasterize(list(event.geometry), out_shape=shape, transform=transform).astype(bool) \
        & inside
    detected = np.isin(final, CHANGE) & inside
    out = {
        "product": prod.name, "run": run.name,
        "coverage_of_reference_area": round(coverage, 3),
        "reference_event_types": sorted(set(event["obj_desc"].astype(str))),
        "flood_extent": {
            "all_confidence": scores(detected, ref),
            "medium_and_high": scores(detected & (conf >= 2), ref),
            "high_only": scores(detected & (conf == 3), ref),
        },
    }
    if seg is not None:
        out["flood_extent"]["own_unet_new_water"] = scores(seg & inside, ref)
    # why were reference pixels missed? (explanation only; thresholds stay frozen)
    missed = ref & ~detected
    names = {0: "no valid radar view (layover/shadow, all tracks)", 1: "no significant change",
             5: "already water before (permanent water)",
             6: "outside our terrain-plausible zone (HAND/slope)"}
    if missed.any():
        vals, counts = np.unique(final[missed], return_counts=True)
        out["missed_reference_pixels_by_reason"] = {
            names.get(int(v), f"class {int(v)}"): round(int(c) / int(missed.sum()), 3)
            for v, c in zip(vals, counts, strict=True)}

    # buildings: EMS graded points vs our potentially affected OSM buildings
    bu = layer(prod, "builtUpP").to_crs(crs)
    bu = bu[bu.geometry.within(covered)]
    ems_dmg = bu[bu["damage_gra"].isin(DAMAGED)]
    mine = ours["buildings"][ours["buildings"].geometry.centroid.within(covered)]
    near_mine = gpd.sjoin_nearest(ems_dmg[["geometry"]], mine[["geometry"]], how="left",
                                  max_distance=NEAR_BUILDING_M) if not mine.empty and \
        not ems_dmg.empty else None
    found = 0 if near_mine is None else \
        int(near_mine[~near_mine.index.duplicated()]["index_right"].notna().sum())
    near_ems = gpd.sjoin_nearest(mine[["geometry"]], bu[["geometry"]], how="left",
                                 max_distance=NEAR_BUILDING_M) if not mine.empty and \
        not bu.empty else None
    confirmed = 0 if near_ems is None else \
        int(near_ems[~near_ems.index.duplicated()]["index_right"].notna().sum())
    out["buildings"] = {
        "reference_destroyed_or_damaged": len(ems_dmg),
        "reference_possibly_damaged": int((bu["damage_gra"] == "Possibly damaged").sum()),
        "ours_potentially_affected": len(mine),
        "reference_damaged_found_by_us": found,
        "recall": round(found / len(ems_dmg), 3) if len(ems_dmg) else None,
        "ours_near_any_reference_graded_building": confirmed,
        "precision_proxy": round(confirmed / len(mine), 3) if len(mine) else None,
        "tolerance_m": NEAR_BUILDING_M,
    }

    # roads: EMS destroyed/damaged road length vs our affected road pieces
    tr = layer(prod, "transportationL").to_crs(crs)
    tr = tr[tr.geometry.intersects(covered)]
    bad = tr[tr["damage_gra"].isin(DAMAGED)].copy()
    bad["geometry"] = bad.geometry.intersection(covered)
    zone = ours["polygons"].to_crs(crs).buffer(10).union_all() \
        if not ours["polygons"].empty else None
    bad_len = float(bad.length.sum())
    in_zone = float(bad.geometry.intersection(zone).length.sum()) if zone is not None else 0.0
    my_roads = ours["roads"].copy()
    my_roads["geometry"] = my_roads.geometry.intersection(covered)
    my_len = float(my_roads.length.sum())
    near_bad = float(my_roads.geometry.intersection(bad.buffer(NEAR_ROAD_M).union_all())
                     .length.sum()) if not bad.empty else 0.0
    out["roads"] = {
        "reference_destroyed_or_damaged_km": round(bad_len / 1000, 2),
        "share_of_it_inside_our_detected_area": round(in_zone / bad_len, 3) if bad_len else None,
        "ours_affected_km": round(my_len / 1000, 2),
        "share_of_ours_near_reference_damage": round(near_bad / my_len, 3) if my_len else None,
    }
    # key breaks of our cut-off analysis: are they on a road EMS graded destroyed/damaged?
    if ours["breaks"] is not None and not ours["breaks"].empty and not bad.empty:
        brk = ours["breaks"].to_crs(crs)
        brk = brk[brk.geometry.within(covered)]
        if not brk.empty:
            d = brk.geometry.apply(lambda p: bad.distance(p).min())
            out["cutoff_key_breaks"] = {"in_reference_area": len(brk),
                                        "within_100m_of_reference_damaged_road":
                                            int((d <= 100).sum())}
    _render(prod, covered, final, conf, transform, ref, ems_dmg, mine, hydro, crs, out)
    return out


def _render(prod, covered, final, conf, transform, ref, ems_dmg, mine, hydro, crs, out):
    from sampark.viz import hillshade

    minx, miny, maxx, maxy = covered.bounds
    pad = 300
    inv = ~transform
    c0, r0 = inv @ (minx - pad, maxy + pad)
    c1, r1 = inv @ (maxx + pad, miny - pad)
    r0, r1, c0, c1 = max(int(r0), 0), int(r1), max(int(c0), 0), int(c1)
    sl = (slice(r0, r1), slice(c0, c1))
    ext = [transform.c + c0 * transform.a, transform.c + c1 * transform.a,
           transform.f + r1 * transform.e, transform.f + r0 * transform.e]
    with rasterio.open(hydro) as src:
        from rasterio.warp import Resampling, reproject

        dem = np.full(final.shape, np.nan, dtype="float32")
        idx = list(src.descriptions).index("dem_m") + 1
        reproject(src.read(idx), dem, src_transform=src.transform, src_crs=src.crs,
                  dst_transform=transform, dst_crs=crs, resampling=Resampling.bilinear)
    ours = np.isin(final, CHANGE)
    rgba = np.zeros(ours[sl].shape + (4,))
    o, r = ours[sl], ref[sl]
    rgba[o & r] = (0.42, 0.24, 0.6, 0.85)
    rgba[o & ~r] = (0.9, 0.62, 0.0, 0.75)
    rgba[~o & r] = (0.12, 0.39, 0.78, 0.6)
    fig, ax = plt.subplots(figsize=(10, 10), constrained_layout=True)
    ax.imshow(hillshade(dem, abs(transform.a))[sl], cmap="gray", extent=ext)
    ax.imshow(rgba, extent=ext, interpolation="nearest")
    gpd.GeoSeries([covered], crs=crs).boundary.plot(ax=ax, color="black", lw=1)
    if not mine.empty:
        c = mine.geometry.centroid
        ax.scatter(c.x, c.y, s=4, color="#d7191c", zorder=4)
    if not ems_dmg.empty:
        ax.scatter(ems_dmg.geometry.x, ems_dmg.geometry.y, s=6, facecolors="none",
                   edgecolors="black", linewidths=0.5, zorder=5)
    ax.set_xlim(ext[0], ext[1])
    ax.set_ylim(ext[2], ext[3])
    fe = out["flood_extent"]["all_confidence"]
    b = out["buildings"]
    ax.set_title(f"{prod.name}: ours vs EMSR927 reference (checking only)\n"
                 f"extent IoU {fe['iou']}, precision {fe['precision']}, recall {fe['recall']}; "
                 f"damaged buildings found {b['reference_damaged_found_by_us']}/"
                 f"{b['reference_destroyed_or_damaged']}", fontsize=10)
    ax.legend(handles=[Patch(color=(0.42, 0.24, 0.6), label="both"),
                       Patch(color=(0.9, 0.62, 0.0), label="ours only"),
                       Patch(color=(0.12, 0.39, 0.78), label="reference only"),
                       Line2D([], [], marker="o", ls="", color="#d7191c", ms=4,
                              label="our potentially affected OSM building"),
                       Line2D([], [], marker="o", ls="", mfc="none", mec="black", ms=5,
                              label="reference: destroyed/damaged building")],
              loc="lower right", fontsize=8)
    credit = (attribution.EMS_VALIDATION_CREDIT + " " + attribution.footer_text())
    fig.text(0.5, -0.01, "\n".join(textwrap.wrap(credit, 160)), ha="center", va="top",
             fontsize=6.5, color="#444444")
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / f"{prod.name}.png", dpi=110, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    runs = [Path(a) for a in sys.argv[1:]]
    if not runs:
        print(__doc__)
        return 1
    results = []
    for prod in sorted(p for p in EMS.iterdir() if p.is_dir() and "_GRA_" in p.name):
        best = None
        for run in runs:
            r = compare(prod, run)
            if r and (best is None or r["coverage_of_reference_area"] >
                      best["coverage_of_reference_area"]):
                best = r
        results.append(best)
        fe = best.get("flood_extent", {}).get("all_confidence", {})
        print(f"{prod.name:32} coverage {best['coverage_of_reference_area']:.0%}  "
              f"extent IoU {fe.get('iou')}  P {fe.get('precision')}  R {fe.get('recall')}")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "metrics.json").write_text(json.dumps({
        "method_frozen": {"config_sha256_prefix": "f6062fb0f6df0693", "commit": "281fd85"},
        "credit": attribution.EMS_VALIDATION_CREDIT, "products": results}, indent=2),
        encoding="utf-8")
    print(f"wrote {OUT / 'metrics.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
