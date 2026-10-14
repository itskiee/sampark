# M9 explainer: unseen-place testing, fixes, and the submission package

M9 turns the working system into the submission. The author asked for "more than 10 unseen
places" before submitting, every deliverable met, and nothing done in a hurry. This file
explains what was tested, what broke, how it was fixed, and why.

---

## 1. The unseen-place campaign (`scripts/run_unseen_places.py`)
- **What:** 13 places and dates the system had never seen, run exactly as a judge would
  (`python -m floodmap run --event …`, a separate process each time), plus 8 invalid inputs.
  The runner records every stage's status, time and message and the key numbers, all read
  from each run's results.json: `docs/testing/unseen_places.md`.
- **Places:** Himalaya (Chamoli 2021, Kullu–Manali 2023, Swat 2022, Kathmandu 2024, Wayanad
  2024), Europe (Ahr 2021, Slovenia 2023, Emilia-Romagna 2023, Thessaly 2023), South America
  (Porto Alegre 2024), Bangladesh (Sylhet 2022), a desert city (Dubai 2024) and a no-flood
  control (Pokhara, dry season 2025).
- **Boxes** are approximate, drawn from general geography (towns and rivers), never from a
  flood or damage map.
- **What it is not:** an accuracy test. These places have no reference map in this project;
  accuracy is measured once, on the case study (`validation/results.md`). Here the question
  is: does the system run, say clearly what it could not do, and give plausible output?
- **Cost:** the Copernicus credit balance went from 9,212 to 9,072 during the second half of
  the campaign (about 25 credits per new area). Repeated runs come from the local cache.

## 2. What the campaign found, and what was done

| Finding | Where | What was done |
|---|---|---|
| **Huge floods were extremely slow.** Storm Daniel's flood map: 755 km², 89,114 polygons, 1 million vertices. Damage took 60 min; the cut-off check was estimated at hours. | Thessaly 2023 | **Fixed, identical results.** Flood polygons are split once into small pieces that exactly tile them (like PostGIS ST_Subdivide), and the cut-off check puts road segments in the spatial index with each flood polygon as a prepared query. Measured on Thessaly: road cutting 800 ms → 1 ms per road; cut-off check ~166 min → 38 s. Tests compare against the old method. |
| **One dropped connection failed a stage.** "Connection broken: IncompleteRead" in the middle of the Copernicus DEM download. | Kathmandu 2024 | **Fixed.** One download function for radar, terrain and Sentinel-2 retries transient problems (busy, broken connection, timeout, temporary server error) and fails at once on real errors; half-written files are never used. Tested. |
| **A dense city ran out of memory.** The landing-zone stage passed all 454,718 buffered buildings to the rasteriser at once: > 6 GB on a 7.7 GB laptop, swapping. | Kathmandu 2024 | **Fixed, identical results.** Buildings are rasterised in batches of 20,000 and combined (same circle resolution as before: a first version used fewer segments and changed one of 747 Trishuli sites, which the comparison caught; the test now compares with the original method). Kathmandu landing zones: 37 s. |
| **Dates outside Sentinel-1** gave a generic "no scenes" message. | invalid inputs | **Fixed:** "… is before Sentinel-1 began routine observations (October 2014)" / "… is today or in the future". |
| **A Python traceback on the console** when a stage failed. | Open sea | **Fixed.** The console shows one line with an error number and "details in logs/floodmap.log"; the log file keeps the full traceback. Tested. |
| **Technical message over open sea** ("NoDataAvailable … load_stac"). The Copernicus DEM covers land only. | Open sea | **Fixed.** "No Copernicus DEM elevation data for this area (open sea, or outside the DEM coverage)…". |
| **No Sentinel-1 pair at all.** Over Porto Alegre there is no radar image between February and the flood (2024, one satellite flying). | Porto Alegre 2024 | **Documented, no code change (design decision).** The system says so clearly. Mapping from Sentinel-2 alone is future work. |
| **A false-alarm floor without a flood:** scattered field and shoreline changes, mostly low confidence. | Pokhara control | **Documented, not tuned.** Changing thresholds after the EMSR927 check would break the frozen-method promise. It is why every result carries a confidence level and "needs field verification". |
| **Deserts and flat cities:** dry sand is as dark as water to radar (labelled "permanent water"); rain changes sand and city surfaces everywhere; in dense flat cities scattered detections cut many roads. | Dubai 2024 | **Documented** as a limitation: the method is built for river-valley floods. |
| **Sparse pre-event OSM:** only 4 buildings mapped in the Swat valley before the 2022 flood; 217 around Chamoli in 2021. | Swat, Chamoli | **Documented** (OSM completeness limitation, as for Derna in M7). |

**Proof that the fixes changed no result:** the Trishuli case study was re-run with all fixes
and compared number by number with the original run (flood area by confidence, buildings,
roads, bridges, settlements, cut-off by network, priority tiers, landing-zone sites and every
offered option, later passes, the model's new water, the flood path): **all identical**.
Thessaly after the fixes: damage 5.6 min (was 60 min) with the same numbers (4,004 of 7,138
buildings, 5,139.0 km of road); cut-off 1.9 min (did not finish in 40 min before).

## 3. Design decisions taken in M9
- **Later satellite passes are optional for a new analysis** (`multidate.enabled: false`).
  - Why: they cost 3.5–8 min per run (30–40 % of a live run), answer a follow-up question
    (is the water going down, is a lake forming?), and usually do not exist yet right after a
    flood. Copernicus EMS works the same way: a first map, then monitoring updates.
  - How it stays visible: a checkbox in "New analysis", a "Track later passes for this area"
    button in the tab (reuses all cached downloads), `--later-passes` on the command line,
    and the case study keeps it on in its event file.
  - The other extras (priority, landing zones, field package) take seconds and belong to the
    first answer, so they stay on.
- **Name: Sampark** (सम्पर्क, "connection" in Hindi and Nepali; Nepali news calls cut-off
  villages सम्पर्कविहीन). Display name only; the code package stays `floodmap`.
- **Sentinel-2-only fallback: not built** (risk the night before submission; documented).
- **The AI model stays as it is.** Four improvement attempts, all decided by validation only,
  did not beat it (M5 explainer, `models/segmentation/experiments.json`).

## 4. Other M9 work
- **`doctor` checks processing itself:** a tiny Copernicus DEM request proves login, credits
  and the processing service work before a demo (6 s).
- **Situation report always fits one page:** text is measured on the page and shrinks or lists
  fewer items until it fits (the full lists stay in the text version and the dashboard).
- **Leftover LLM settings removed** (no external AI service is used).
- **Dashboard:** uses each event's own settings; lists the unseen-place events.
- **Deliverables:** README for judges, report draft builder (`report/build_report.py`, every
  number generated), video kit (`report/video/`: script, captions, title and end cards),
  Devpost images (`report/devpost/`).

## 5. If a judge asks
- *"Did you test it anywhere else?"* Thirteen unseen places on four continents plus invalid
  inputs; every finding and fix is listed above, including what still does not work.
- *"Why is later-pass tracking off by default?"* The first answer must be fast; monitoring is
  a follow-up, as in Copernicus EMS.
- *"What happens if the radar data is missing?"* The system says so (Porto Alegre) instead of
  inventing a map.
