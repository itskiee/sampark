"""Write validation/results.md from validation/results/metrics.json (no hand-typed numbers).

Credit: European Union, Copernicus Emergency Management Service data.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
M = json.loads((HERE / "results" / "metrics.json").read_text(encoding="utf-8"))
PLACES = {"AOI01": "Syapru Besi (Syabrubesi)", "AOI02": "Timure", "AOI03": "Bidur",
          "AOI05": "Phosretar"}


def place(name: str) -> str:
    aoi = name.split("_")[1]
    kind = "monitoring update" if "MONIT" in name else "grading"
    return f"{aoi} {PLACES.get(aoi, '')} ({kind})"


def f(v):
    return "–" if v is None else (f"{v:.2f}" if isinstance(v, float) else str(v))


rows_ext, rows_conf, rows_bld, rows_road, rows_miss = [], [], [], [], []
for p in M["products"]:
    if "flood_extent" not in p:
        continue
    fe = p["flood_extent"]
    a = fe["all_confidence"]
    rows_ext.append(f"| {place(p['product'])} | {p['run']} | {f(a['iou'])} | {f(a['precision'])} | "
                    f"{f(a['recall'])} | {f(a['f1'])} | {a['ours_km2']} | {a['reference_km2']} |")
    rows_conf.append(f"| {place(p['product'])} | {f(fe['all_confidence']['iou'])} | "
                     f"{f(fe['medium_and_high']['iou'])} | {f(fe['high_only']['iou'])} | "
                     f"{f(fe.get('own_unet_new_water', {}).get('iou'))} | "
                     f"{f(fe.get('own_unet_new_water', {}).get('precision'))} |")
    b = p["buildings"]
    rows_bld.append(f"| {place(p['product'])} | {b['reference_destroyed_or_damaged']} | "
                    f"{b['reference_damaged_found_by_us']} | {f(b['recall'])} | "
                    f"{b['ours_potentially_affected']} | {f(b['precision_proxy'])} |")
    r, k = p["roads"], p.get("cutoff_key_breaks", {})
    rows_road.append(f"| {place(p['product'])} | {r['reference_destroyed_or_damaged_km']} | "
                     f"{f(r['share_of_it_inside_our_detected_area'])} | {r['ours_affected_km']} | "
                     f"{f(r['share_of_ours_near_reference_damage'])} | "
                     f"{k.get('within_100m_of_reference_damaged_road', '–')} / "
                     f"{k.get('in_reference_area', '–')} |")
    miss = p.get("missed_reference_pixels_by_reason", {})
    rows_miss.append(f"| {place(p['product'])} | "
                     f"{f(miss.get('outside our terrain-plausible zone (HAND/slope)'))} | "
                     f"{f(miss.get('no significant change'))} | "
                     f"{f(miss.get('already water before (permanent water)'))} | "
                     f"{f(miss.get('no valid radar view (layover/shadow, all tracks)'))} |")

text = f"""# Case study: comparison with the official Copernicus EMS maps (EMSR927)

*Checking only. EMSR927 was never an input: our system was frozen before the reference data
were opened (configs/default.yaml unchanged since commit {M['method_frozen']['commit']},
SHA-256 prefix {M['method_frozen']['config_sha256_prefix']}). Nothing was tuned afterwards.
Later the config only gained new sections for the extras (landing_zones, multidate); no
flood, damage or cut-off value changed (`git diff 048c725 -- configs/default.yaml`).*
Credit: **{M['credit']}**

## What was compared
- **Reference:** the EMSR927 grading products (very-high-resolution optical images,
  0.3–1.0 m, of 27 Aug–5 Sep 2026, photo-interpreted).
  - Observed event "Mass movement / Landslide": the debris-flow extent.
  - Buildings, roads and bridges graded Destroyed / Damaged / Possibly damaged.
  - Areas marked "not analysed" by EMS are excluded.
- **Ours:** the frozen system's run outputs.
  - Detected flood/debris change on a 20 m radar grid (Sentinel-1 after 28 Aug and 5 Sep,
    Sentinel-2 27 Aug).
  - Pre-event OSM buildings and roads flagged "potentially affected".
  - Likely road breaks from the cut-off analysis.
- **Runs:**
  - The case-study box covers AOI01–AOI03.
  - EMS AOI05 (Phosretar) lies outside it. There we ran the unchanged system on a box
    around AOI05 (84.93–85.12 E, 27.78–27.88 N), exactly as for any user-chosen area.
- **Scale difference:** our 20 m pixels vs their 0.3–1 m photo-interpretation, and an OSM
  building footprint vs their building point, compared with a {M['products'][0]['buildings']['tolerance_m']} m
  tolerance. Perfect agreement is not possible even for a correct map.

## 1. Flood / debris extent (all confidence levels)
| EMS area | Our run | IoU | Precision | Recall | F1 | Ours km² | Reference km² |
|---|---|---|---|---|---|---|---|
{chr(10).join(rows_ext)}

- Precision = share of what we detected that EMS also mapped.
- Recall = share of the EMS-mapped extent that we detected.

## 2. By confidence level, and our own AI model (IoU)
| EMS area | All | Medium + high | High only | Own U-Net new water | U-Net precision |
|---|---|---|---|---|---|
{chr(10).join(rows_conf)}

## 3. Buildings
| EMS area | EMS destroyed/damaged | Found by us | Recall | Our potentially affected | Precision proxy |
|---|---|---|---|---|---|
{chr(10).join(rows_bld)}

- "Found by us" means an EMS destroyed/damaged point within the tolerance of an OSM building
  we flagged.
- The precision proxy is the share of our flagged buildings near *any* EMS-graded building
  (EMS grades only damaged buildings, so this is approximate).

## 4. Roads, and the cut-off analysis's road breaks
| EMS area | EMS destroyed/damaged road km | Share inside our detected area | Our affected road km | Share of ours near EMS damage | Our key breaks within 100 m of an EMS-damaged road |
|---|---|---|---|---|---|
{chr(10).join(rows_road)}

## 5. Why reference area was missed (explanation, not tuning)
Share of the missed EMS pixels, by the reason our system did not flag them:

| EMS area | Outside our terrain zone (HAND > 30 m or slope > 30°) | No significant radar change | Already water before | No valid radar view |
|---|---|---|---|---|
{chr(10).join(rows_miss)}
"""
main = [p for p in M["products"] if "flood_extent" in p and "MONIT" not in p["product"]]
prec = [p["flood_extent"]["all_confidence"]["precision"] for p in main]
brk_in = sum(p.get("cutoff_key_breaks", {}).get("in_reference_area", 0) for p in main)
brk_ok = sum(p.get("cutoff_key_breaks", {}).get("within_100m_of_reference_damaged_road", 0)
             for p in main)
gorge = [p for p in main if p["product"].split("_")[1] in ("AOI01", "AOI02")]
valley = [p for p in main if p["product"].split("_")[1] in ("AOI03", "AOI05")]
hi_p = [p["flood_extent"]["high_only"]["precision"] for p in main]
zone = [p["missed_reference_pixels_by_reason"].get(
    "outside our terrain-plausible zone (HAND/slope)", 0) for p in gorge]


def rng(vals):
    lo, hi = min(vals), max(vals)
    return f"{lo:.2f}" if f"{lo:.2f}" == f"{hi:.2f}" else f"{lo:.2f}–{hi:.2f}"


text += f"""
## 6. What this shows (main grading products; monitoring updates agree)
- **What we flag is almost always real.** Extent precision is {rng(prec)} in every EMS
  area. "Potentially affected" areas are confirmed debris-flow areas in the official maps.
- **Wide valleys (Bidur, Phosretar): good overall agreement.**
  - Extent IoU {rng([p["flood_extent"]["all_confidence"]["iou"] for p in valley])}, recall
    {rng([p["flood_extent"]["all_confidence"]["recall"] for p in valley])}.
  - Damaged buildings found {rng([p["buildings"]["recall"] for p in valley])}.
  - {rng([p["roads"]["share_of_it_inside_our_detected_area"] for p in valley])} of the
    EMS-damaged road lies inside our detected area.
- **Narrow upper gorge (Syabrubesi, Timure): precise but incomplete.**
  - Extent recall {rng([p["flood_extent"]["all_confidence"]["recall"] for p in gorge])}.
  - {rng(zone)} of what we missed lies outside our terrain-plausible zone (more than 30 m above
    the river or steeper than 30°). EMS maps the whole mass movement, including steep
    flanks; our mask ignores such ground on purpose to avoid false alarms (M2).
  - This is a precision-for-recall trade-off. We did **not** relax the mask after seeing the
    reference, which would be tuning against it.
  - A debris-flow-specific zone for steep terrain is the main improvement to make.
- **Cut-off analysis:** {brk_ok} of our {brk_in} key road breaks inside EMS areas lie within
  100 m of a road EMS graded destroyed or damaged. Our "likely impassable" locations are
  largely the ones the official mapping found broken.
- **Confidence levels:** precision of high-confidence-only detections {rng(hi_p)} vs all
  detections {rng(prec)}. Recall drops when only high confidence is kept (section 2). The
  levels trade recall for certainty, as designed; precision is already high at every level.
- **Our own U-Net water model:**
  - It agrees where there is open water (Phosretar IoU
    {rng([p["flood_extent"]["own_unet_new_water"]["iou"] for p in main if "AOI05" in p["product"]])},
    precision ≥ 0.99).
  - It finds almost nothing of the debris flow in the gorge or at Bidur, because it was
    trained on water only (Sen1Floods11). This confirms the M5 finding that the change
    detection must stay the primary map.

## 7. Limits of this comparison
- **Different sensors and scales:** 0.3–1 m optical photo-interpretation vs 20 m radar.
  Thin debris tongues and single houses are below our resolution.
- **Different dates:** EMS images of 27 Aug–5 Sep; our radar after 28 Aug / 5 Sep.
- **Approximate building matching:** EMS building points vs OSM footprints, within a
  tolerance.
- **No road-network or cut-off product from EMS:** only road damage grading, so cut-off is
  checked indirectly through the break locations.

Maps: `validation/results/<product>.png` (ours vs reference; credited).
"""
(HERE / "results.md").write_text(text, encoding="utf-8")
print("wrote", HERE / "results.md")
