# Case study: comparison with the official Copernicus EMS maps (EMSR927)

*Checking only. EMSR927 was never an input: our system was frozen before the reference data
were opened (configs/default.yaml unchanged since commit 281fd85,
SHA-256 prefix f6062fb0f6df0693). Nothing was tuned afterwards.
Later the config only gained new sections for the extras (landing_zones, multidate); no
flood, damage or cut-off value changed (`git diff 281fd85 -- configs/default.yaml`).*
Credit: **European Union, Copernicus Emergency Management Service data.**

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
  building footprint vs their building point, compared with a 15 m
  tolerance. Perfect agreement is not possible even for a correct map.

## 1. Flood / debris extent (all confidence levels)
| EMS area | Our run | IoU | Precision | Recall | F1 | Ours km² | Reference km² |
|---|---|---|---|---|---|---|---|
| AOI01 Syapru Besi (Syabrubesi) (grading) | 20261013T073521Z_6d540fab | 0.30 | 0.98 | 0.31 | 0.47 | 0.346 | 1.108 |
| AOI02 Timure (grading) | 20261013T073521Z_6d540fab | 0.30 | 1.00 | 0.30 | 0.46 | 0.387 | 1.295 |
| AOI03 Bidur (monitoring update) | 20261013T073521Z_6d540fab | 0.78 | 0.94 | 0.81 | 0.87 | 6.082 | 7.016 |
| AOI03 Bidur (grading) | 20261013T073521Z_6d540fab | 0.78 | 0.93 | 0.82 | 0.88 | 5.198 | 5.887 |
| AOI05 Phosretar (monitoring update) | 20261013T084115Z_5dc84a98 | 0.69 | 0.93 | 0.73 | 0.82 | 5.052 | 6.388 |
| AOI05 Phosretar (grading) | 20261013T084115Z_5dc84a98 | 0.68 | 0.91 | 0.72 | 0.81 | 3.811 | 4.789 |

- Precision = share of what we detected that EMS also mapped.
- Recall = share of the EMS-mapped extent that we detected.

## 2. By confidence level, and our own AI model (IoU)
| EMS area | All | Medium + high | High only | Own U-Net new water | U-Net precision |
|---|---|---|---|---|---|
| AOI01 Syapru Besi (Syabrubesi) (grading) | 0.30 | 0.29 | 0.23 | 0.00 | – |
| AOI02 Timure (grading) | 0.30 | 0.30 | 0.21 | 0.00 | – |
| AOI03 Bidur (monitoring update) | 0.78 | 0.78 | 0.76 | 0.04 | 1.00 |
| AOI03 Bidur (grading) | 0.78 | 0.78 | 0.77 | 0.05 | 1.00 |
| AOI05 Phosretar (monitoring update) | 0.69 | 0.69 | 0.66 | 0.27 | 0.99 |
| AOI05 Phosretar (grading) | 0.68 | 0.68 | 0.65 | 0.26 | 0.99 |

## 3. Buildings
| EMS area | EMS destroyed/damaged | Found by us | Recall | Our potentially affected | Precision proxy |
|---|---|---|---|---|---|
| AOI01 Syapru Besi (Syabrubesi) (grading) | 355 | 170 | 0.48 | 114 | 0.90 |
| AOI02 Timure (grading) | 405 | 131 | 0.32 | 133 | 0.78 |
| AOI03 Bidur (monitoring update) | 2725 | 2190 | 0.80 | 2013 | 0.87 |
| AOI03 Bidur (grading) | 2046 | 1662 | 0.81 | 1565 | 0.87 |
| AOI05 Phosretar (monitoring update) | 500 | 332 | 0.66 | 583 | 0.58 |
| AOI05 Phosretar (grading) | 361 | 264 | 0.73 | 488 | 0.69 |

- "Found by us" means an EMS destroyed/damaged point within the tolerance of an OSM building
  we flagged.
- The precision proxy is the share of our flagged buildings near *any* EMS-graded building
  (EMS grades only damaged buildings, so this is approximate).

## 4. Roads, and the cut-off analysis's road breaks
| EMS area | EMS destroyed/damaged road km | Share inside our detected area | Our affected road km | Share of ours near EMS damage | Our key breaks within 100 m of an EMS-damaged road |
|---|---|---|---|---|---|
| AOI01 Syapru Besi (Syabrubesi) (grading) | 6.15 | 0.65 | 4.92 | 0.86 | 4 / 4 |
| AOI02 Timure (grading) | 9.52 | 0.42 | 4.74 | 0.63 | 4 / 4 |
| AOI03 Bidur (monitoring update) | 41.2 | 0.92 | 50.0 | 0.78 | 26 / 28 |
| AOI03 Bidur (grading) | 31.57 | 0.94 | 42.23 | 0.75 | 19 / 22 |
| AOI05 Phosretar (monitoring update) | 20.57 | 0.82 | 26.49 | 0.62 | 15 / 26 |
| AOI05 Phosretar (grading) | 16.34 | 0.78 | 19.0 | 0.63 | 12 / 22 |

## 5. Why reference area was missed (explanation, not tuning)
Share of the missed EMS pixels, by the reason our system did not flag them:

| EMS area | Outside our terrain zone (HAND > 30 m or slope > 30°) | No significant radar change | Already water before | No valid radar view |
|---|---|---|---|---|
| AOI01 Syapru Besi (Syabrubesi) (grading) | 0.72 | 0.27 | 0.00 | 0.01 |
| AOI02 Timure (grading) | 0.66 | 0.32 | – | 0.02 |
| AOI03 Bidur (monitoring update) | 0.41 | 0.57 | 0.02 | – |
| AOI03 Bidur (grading) | 0.35 | 0.62 | 0.02 | – |
| AOI05 Phosretar (monitoring update) | 0.09 | 0.71 | 0.20 | – |
| AOI05 Phosretar (grading) | 0.09 | 0.73 | 0.18 | – |

## 6. What this shows (main grading products; monitoring updates agree)
- **What we flag is almost always real.** Extent precision is 0.91–1.00 in every EMS
  area. "Potentially affected" areas are confirmed debris-flow areas in the official maps.
- **Wide valleys (Bidur, Phosretar): good overall agreement.**
  - Extent IoU 0.68–0.78, recall
    0.72–0.82.
  - Damaged buildings found 0.73–0.81.
  - 0.78–0.94 of the
    EMS-damaged road lies inside our detected area.
- **Narrow upper gorge (Syabrubesi, Timure): precise but incomplete.**
  - Extent recall 0.30–0.31.
  - 0.66–0.72 of what we missed lies outside our terrain-plausible zone (more than 30 m above
    the river or steeper than 30°). EMS maps the whole mass movement, including steep
    flanks; our mask ignores such ground on purpose to avoid false alarms (M2).
  - This is a precision-for-recall trade-off. We did **not** relax the mask after seeing the
    reference, which would be tuning against it.
  - A debris-flow-specific zone for steep terrain is the main improvement to make.
- **Cut-off analysis:** 39 of our 52 key road breaks inside EMS areas lie within
  100 m of a road EMS graded destroyed or damaged. Our "likely impassable" locations are
  largely the ones the official mapping found broken.
- **Confidence levels:** precision of high-confidence-only detections 0.94–1.00 vs all
  detections 0.91–1.00. Recall drops when only high confidence is kept (section 2). The
  levels trade recall for certainty, as designed; precision is already high at every level.
- **Our own U-Net water model:**
  - It agrees where there is open water (Phosretar IoU
    0.26,
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
