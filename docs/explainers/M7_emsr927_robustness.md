# M7 explainer: case-study comparison with EMSR927, and robustness

Brief: *"Case study: Map the August 2026 Trishuli flood and compare your results with the
official Copernicus Emergency Management Service maps for this event (activation
EMSR927)"*, and *"The system must run end-to-end from raw satellite data for any area and
date the judges choose."*

---

## 1. Rules for using the reference maps (author check 2026-10-13, `docs/compliance.md` §11)
- **Checking only:** EMSR927 "may only be used to check your results". The packages live in
  `validation/data/` (git-ignored); the scripts live only in `validation/`.
- **Pipeline code cannot read them:** pipeline code (`src/`, `app/`, `scripts/`) is blocked
  from them by the data-rules scanner. It even rejected the word in a code comment.
- **Frozen before opening:** `configs/default.yaml` unchanged since commit 281fd85 (SHA-256
  prefix `f6062fb0f6df0693`). No threshold, mask, event area or flood-path start was changed
  afterwards.
  - The larger AI model in training was started before the download and is chosen on
    Sen1Floods11 validation data only.
- **Not used:**
  - EMSR927's OSM change file (`.osc`) was never extracted.
  - Its baseline layers are not used in our analysis.
  - The dashboard does not show the reference.
- **Credit** on every comparison output: "European Union, Copernicus Emergency Management
  Service data". File checksums: `validation/EMSR927_SHA256SUMS.txt`.

## 2. What EMSR927 contains
- **Activation:** "Flood in Nepal", activated 26 Aug 2026; 6 areas, 4 with damage grading:
  - AOI01 Syapru Besi;
  - AOI02 Timure;
  - AOI03 Bidur (+ monitoring update);
  - AOI05 Phosretar (+ monitoring update).
- **Observed event:** "Mass movement / Landslide", the debris flow.
- **Damage grading:** buildings, roads and bridges graded Destroyed / Damaged / Possibly
  damaged, by photo-interpretation of 0.3–1.0 m optical images from 27 Aug–5 Sep 2026.
- **AOI05:** outside our case-study box. We ran the **unchanged system** on a box around it,
  exactly as for any user-chosen area, and say so.

## 3. Method (`validation/compare_emsr927.py`)
- **Evaluation region:** each EMS area minus its "not analysed" parts, inside our run's area.
- **Extent:** EMS observed event vs our detected change (classes new water / backscatter
  decrease / increase) on our 20 m grid. Reported as IoU, precision, recall and F1, per
  confidence level, and for our own U-Net.
- **Buildings:** EMS destroyed/damaged points found near an OSM building we flagged (15 m
  tolerance: point vs footprint). The precision proxy is our flagged buildings near any
  EMS-graded building.
- **Roads:** share of EMS-damaged road length inside our detected area, and share of our
  affected road near EMS damage.
- **Cut-off check:** our key road breaks within 100 m of an EMS-damaged road. EMS has no
  network product, so this is the closest available check.
- **Explanation of misses:** for every missed EMS pixel, the reason in our map (outside the
  terrain zone, no change, already water, no radar view). This *explains* the misses; it
  does not tune anything.
- **Reproducible:** tables in `validation/results.md` are generated from
  `results/metrics.json` by `make_results_md.py`, so no number is typed by hand.

## 4. Results (full tables: `validation/results.md`)
| EMS area (grading) | Extent IoU | Precision | Recall | Damaged buildings found | Key breaks on EMS-damaged roads |
|---|---|---|---|---|---|
| AOI01 Syabrubesi | 0.30 | 0.98 | 0.31 | 170 / 355 (0.48) | 4 / 4 |
| AOI02 Timure | 0.30 | 1.00 | 0.30 | 131 / 405 (0.32) | 4 / 4 |
| AOI03 Bidur | **0.78** | 0.93 | **0.82** | 1,662 / 2,046 (**0.81**) | 19 / 22 |
| AOI05 Phosretar | **0.68** | 0.91 | 0.72 | 264 / 361 (0.73) | 12 / 22 |

- **What we flag is real:** extent precision 0.91–1.00 everywhere.
- **Wide valleys agree well:** IoU 0.68–0.78.
- **Our key breaks match official damage:** 39 of 52 key road breaks lie within 100 m of a
  road EMS graded destroyed or damaged.
- **Narrow gorge: precise but incomplete** (recall about 0.30). 66–72 % of the misses lie
  outside our terrain-plausible zone: steep flanks the debris flow scoured, which the M2
  mask deliberately ignores to avoid false alarms. This is a documented trade-off, **not
  re-tuned**. The improvement to make next is a separate debris-flow zone for steep terrain.
- **The M3 concern resolved:** in M3 we worried that 79 % of flagged buildings were in the
  wider lower valley (perhaps monsoon paddies). EMS AOI03 Bidur grades 2,046 buildings
  destroyed/damaged there, and we found 81 % of them. The lower-valley damage is real.
- **Our U-Net:** IoU 0.26 (precision 0.99) where there is open water (Phosretar), about 0
  in the debris-dominated gorge. Trained on water only, it is a second opinion, as M5
  concluded.

## 5. Robustness: unseen areas and dates (full pipeline, unchanged system, 2026-10-13)
Areas were chosen **independently of EMSR927**, to test the brief's "any area and date the
judges choose". They include two events outside the Himalaya.

| Event (date) | Area | Sentinel-2 | Potentially affected | OSM buildings (pre-event) | Buildings potentially affected | Likely cut off by road | Failed stages | First run |
|---|---|---|---|---|---|---|---|---|
| Trishuli case study (2026-08-26) | 1,792 km² | partly clear (31 %) | 15.60 km² | 107,091 | 3,332 | 215 / 2,866 | none | cached |
| Phosretar, EMS AOI05 area (2026-08-26) | 207 km² | clear | 6.46 km² | 21,147 | 914 | 108 / 735 | none | 517 s |
| Melamchi, Nepal (2021-06-15) | 183 km² | partly clear | 3.57 km² | 21,472 | 574 | 42 / 736 | none | cached |
| Sikkim / Teesta, India (2023-10-04) | 279 km² | too cloudy (skipped, message) | 2.06 km² | 869 | 304 | 18 / 27 | none | cached |
| **Derna, Libya (2023-09-10)**, dam-break flood | 140 km² | clear | 12.23 km² | **404** | 85 | 5 / 9 | none | 472 s |
| **Valencia, Spain (2024-10-29)**, flash flood | 103 km² | partly clear | 37.40 km² | 18,295 | 3,148 | 73 / 154 | none | 773 s (before the speed fix below) |
| No-flood control, Trishuli (2026-02-15) | 109 km² | clear | 0.09 km² | 2,903 | 26 (0 high) | **0** / 79 | none | cached |

**Findings:**
- **No crash and no failed stage on any area,** including desert, coast and city.
- **Derna:** only 404 OSM buildings existed *before* the flood. Most of the city was mapped
  by volunteers afterwards, and those edits are correctly excluded by the pre-event rule.
  Building and cut-off numbers are only as complete as pre-event OSM (a limitation for the
  report).
- **Valencia (flat lowland):** our U-Net agrees with the change detection at IoU **0.80**,
  the terrain type it was trained on, against about 0.1 in Himalayan gorges.
- **Speed bug found and fixed:** Valencia's damage stage took 388–424 s. Cutting every road
  against one merged flood shape was the cause (345 s in a profile). Each road is now cut
  against only the polygons it touches.
  - Result: **30.8 s**, with identical numbers (3,148 buildings, 545.9 km, 359 bridges).
  - Trishuli is unchanged (3,332 / 80.5 km / 163) and faster (42 s → 28 s).
  - A test proves the new method equals the old one. It also caught a first faulty version
    of the fix, which merged line pieces and double-counted overlaps.
- **No-flood control:** 0 settlements likely cut off; no high-confidence building flagged.
