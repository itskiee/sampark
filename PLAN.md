# PLAN — floodmap (Track B: Mapping Flood Damage from Space)

Deadline: Devpost **Oct 15 2026, 00:15 IST** (end of Oct 14). Full scope, nothing cut.
Order of work is core first (M0–M7), then extras (M8), so the system is always demo-able.
Rule hierarchy and data rules, line by line against the brief: [docs/compliance.md](docs/compliance.md).

**Data sources (owner decision 2026-10-11: follow the brief literally, no risk):** Sentinel-1,
Sentinel-2 and Copernicus DEM only from the **Copernicus Data Space Ecosystem** (named in the
brief); OpenStreetMap only from the **ohsome API** (named in the brief). No Google Earth Engine.

## Milestones

### M0 — Setup (Oct 10–11)
- [x] git repo, `.gitignore`, `.gitattributes`, `.python-version`, venv (Python 3.12)
- [x] dependencies pinned with SHA-256 hashes (`requirements*.txt` via `uv pip compile --universal`)
- [x] working rules, PLAN.md, README skeleton, SECURITY.md, LICENSE, `.env.example`
- [x] package skeleton: config, aoi, results, cache, data_rules, net, catalogue, cdse_client,
      ohsome_client, logging_setup (redaction), attribution, pipeline (`run_pipeline`),
      CLI (`run`, `login`, `doctor`, `config-show`)
- [x] data-rules test: static scanner + scanner self-tests + runtime guards + snapshot rule + git-ignore checks
- [x] redaction tests (logs, tracebacks, results.json, config-show, doctor)
- [x] pre-commit: gitleaks, detect-private-key, large files, ruff, data rules
- [x] CI workflow (ubuntu + windows, read-only token, SHA-pinned actions, gitleaks, pip-audit) + Dependabot
- [x] rework 2026-10-11: Earth Engine removed, Copernicus Data Space (STAC catalogue + openEO) added
- [x] `scripts/check_cdse.py` prints the Sentinel-1 inventory (public catalogue)
- [x] `check_cdse.py` DEM sample (1,666 m near Syabrubesi) + `pytest -m cdse` green (2026-10-11)
- [ ] CI green on GitHub  ← **needs owner's private GitHub repo**

**Done when:** offline tests + pre-commit green; doctor runs without printing secrets; skeleton
run writes results.json; CDSE inventory + DEM sample work; CI green.

### M1 — Sentinel-1 change detection on Trishuli (Oct 11) — DONE, owner GO 2026-10-11
- [x] `s1_pairing`: same-track (relative orbit + pass) pre/post selection from the CDSE catalogue, slice merge, coverage ranking, clean failure
- [x] `s1_change` (CDSE openEO): `sar_backscatter` with Copernicus DEM (`COPERNICUS_30`) terrain correction, pre-event median on CDSE (one request), local speckle filter, log-ratio VV/VH
- [x] download as float32 GeoTIFF (local UTM, adaptive resolution, 429 wait+retry, resumable per-image fallback) + cache (238 s first run → 15 s cached)
- [x] `thresholds`: water-like decrease / debris increase / non-dark decrease; Otsu + bimodality check + fixed fallback; min mapping unit
- [x] `viz`: full + focus PNGs, RGB change composite, attribution footer
- [x] ablation PNGs (no speckle, single pre, VV vs VH, fixed vs Otsu) — `scripts/ablation_s1.py`
- [x] full s1 runs on Melamchi 2021-06-15, Sikkim 2023-10-04 and a no-flood case: all OK, no crash
- [x] unit tests (pairing 12, thresholds 8, local processing + rendering 8)
- [x] explainer `docs/explainers/M1_s1_change_detection.md`
- [ ] offline end-to-end fixture test (moved to M2 together with the terrain stage)
- Proposals for owner: **P16** VH also for decreases; **P17** optional scene-shift correction (see explainer §7c)

Known Trishuli pairs (CDSE catalogue, 2026-10-11): track 85 ascending 16 Aug → 28 Aug;
track 121 descending 19 Aug → 31 Aug; track 19 descending 24 Aug → 5 Sep (all Sentinel-1D, VV+VH).

**Done when / GO-NO-GO:** `run --stages s1` prints pair + stats, second run is served from cache, and
the flood/debris path along the Bhote Koshi near Syabrubesi is clearly visible in the saved focus PNG.

### M2 — DEM masks, flood path, S2, confidence (Oct 11–12) — DONE
- [x] slope, HAND (from GLO-30 via pysheds, locally), radar layover/shadow masks (own geometry; CDSE has no LIA output)
- [x] permanent water from pre-event S1 (no JRC)
- [x] flood-path tracing from any upstream point (official bonus): Trishuli 81.9 km, 1,318 m drop, 50 % on detected change
- [x] S2 L2A from CDSE: SCL cloud mask, MNDWI new water, NDVI vegetation loss; graceful "too cloudy"
- [x] asc + desc track fusion (per-track layover/shadow)
- [x] confidence layer high/medium/low + affected-area polygons (GeoJSON) + hillshade maps
- [x] P16 (VH also for decreases) implemented; P17 re-assessed → not needed after masks → **dropped (owner decision 2026-10-11)**
- [x] offline end-to-end pipeline test (CDSE faked) → CI tests the whole pipeline
- [x] robustness: Melamchi, Sikkim, no-flood control all OK (see M2 explainer)
- [x] explainer `docs/explainers/M2_terrain_s2_confidence.md`
- Settlements along the flood path → M3 (needs pre-event OSM)

**Done when:** masked flood map PNG visibly cleaner than M1; flood path lists settlements downstream
(after M3); confidence raster produced; S2 absent → clear message, pipeline continues.

### M3 — Pre-event OSM + damage overlay (Oct 12) — DONE, committed 08532a7
- [x] ohsome client on the brief's v1 URL: v1 snapshot endpoints now return 403 (ohsome-api #346),
      so the snapshot is rebuilt from `/elementsFullHistory` (versions valid at the snapshot);
      verified equal to ohsome's own snapshot counts; post-snapshot guard; `pytest -m network` check
- [x] layers: buildings (area), roads/bridges, places, health facilities (area + 10 km) — only what the brief names;
      untrusted-text cleaning; no `population` tag; building counts per settlement (population proxy)
- [x] per-layer tiling: first download 737 s → 109 s (same counts); cached ~14 s
- [x] damage overlay: buildings, roads (affected length + pieces for M4), health facilities,
      settlements; class/confidence inherited; "potentially affected" wording
- [x] bridge logic (P8): deck/approach 30 m + changed reach 150 m, evidence per bridge
- [x] flood path lists settlements (km, distance, height above river, low-lying flag, buildings);
      Trishuli start moved to the Bhote Koshi entry point (DEM-derived)
- [x] optional stage inputs (`uses=`): `--stages damage` now includes Sentinel-2 (bug found in M3)
- [x] doctor checks the ohsome feature download; legends moved below maps
- [x] tests: ohsome client 5, osm 7, damage 6, pipeline optional input, offline e2e incl. OSM/damage
- [x] explainer `docs/explainers/M3_osm_damage.md` (ablation: buffers, bridge rule)
- Proposals P18 (settlements from building clusters) and P19 (flood-corridor tag): approved, built in M4

**Done when:** affected-asset counts and layers for Trishuli; empty OSM → clear message.

### M4 — Cut-off analysis + priority ranking (Oct 12) — DONE (awaiting owner review)
- [x] P18 settlements: groups of >= 5 buildings each within 75 m (split above 3 km), named from OSM places
- [x] P19 "on the flood corridor" tag on affected buildings/roads/bridges/settlements
- [x] road graph (scipy sparse, no new dependency; roads densified to 50 m), motorable vs all ways,
      buffered network area + virtual "outside" node (P9); over/under-passes not joined
- [x] before/after reachability to nearest town / hospital / road leaving the area; first break on the
      old route + distance; sentence in the brief's form; key breaks ranked by buildings cut off
- [x] breaks only at medium/high confidence (config); low-confidence = "possibly cut off" (worst case)
- [x] owner review: wording "likely cut off / likely impassable (confidence) / needs field verification";
      destinations defined explicitly; settlement link sensitivity 50/75/100/150 m (buildings stable)
- [x] faster maps (one LineCollection for all roads): cutoff 35 s → 13 s, damage 42 s → 22 s
- [x] owner audit of M1–M3 (2026-10-12): no data-rule violation found (hosts, collections, EMSR isolation,
      population proxy, footers all checked); wording hedged: classes "backscatter decrease/increase
      (likely wet mud / debris / rough debris)", map title "Detected flood & debris (estimate)";
      unused OSM tags dropped; one unsourced outside-knowledge sentence removed from the M4 explainer
- [x] rescue priority: transparent tiers + reasons; priority.csv for field teams
- [x] tests: brief's A/B/C/D example, detour via trail, low-confidence, outside node, overpass, P18 groups,
      offline e2e incl. cutoff/priority
- [x] explainer `docs/explainers/M4_cutoff_priority.md`

**Done when:** output in the brief's form ("Village X cut off from <hospital>; road broken at …").

### M5 — Own flood segmentation model = declared AI component (Oct 13) — DONE (awaiting owner review)
Owner decision 2026-10-13 after re-reading both PDFs ("AI COMPONENT — choose one"): option 1,
**train our own model**; no external AI services, nothing leaves the machine. The LLM copilot
(option 2) is not built; proposals P1–P3 (copilot) are withdrawn.
- [x] Sen1Floods11 hand-labelled S1 chips + labels + official splits (896 files, 0.73 GB, MD5-checked);
      JRC folder refused by the data-rules guard; download only via scripts/fetch_sen1floods11.py
- [x] deps: onnxruntime (runtime), torch CPU (train extra, requirements-train.txt), hash-locked, audited
- [x] small U-Net trained from scratch on CPU (482,593 parameters, 20 m, 30 epochs, 40.8 min), ONNX 1.9 MB
- [x] evaluation: test IoU 0.649 (threshold rule 0.445); unseen Bolivia event IoU 0.648 (rule 0.581); model card
- [x] improvement attempts (rule: validation +0.005): bigger model 0.6285, weight averaging 0.6247, + brightness
      augmentation 0.6242, ensemble 0.6281 vs current 0.6293 → current kept; Bolivia varies 0.536–0.648 between trainings (experiments.json)
- [x] pipeline stage `segmentation`: water before/after → new water in plausible zone; agreement with change detection;
      not counted as an independent confidence source (same image); doctor checks the model file
- [x] Himalayan unseen scenes: poor transfer (radar shadow taken as water, narrow corridor missed), no false
      new water in the no-flood control; EMSR927 comparison of the model in M7
- [x] explainer `docs/explainers/M5_segmentation_model.md`; tests (preprocessing, tiling, real-model smoke, e2e)

### M6 — Dashboard + one-page sit-rep (Oct 13–14) — DONE (awaiting owner review)
- [x] sit-rep: stage `report` + `floodmap report`: A4 PDF/PNG + plain text, numbers only from results.json,
      "not available (reason)" for missing stages, attributions + disclaimer + training citation
- [x] Streamlit dashboard (`floodmap dashboard`): new analysis (event or bbox + date + flood-path point) with live
      progress, previous runs, tabs per brief question, downloads (PDF/CSV/GeoJSON+ODbL), attribution footer
- [x] privacy/safety config: no usage statistics, localhost only, no error details in the browser (tested)
- [x] own maps only (no external tiles); headless tests (no runs, partial run); in-app browser check
- [x] explainer `docs/explainers/M6_sitrep_dashboard.md`

### M7 — EMSR927 comparison + robustness (Oct 13) — DONE (awaiting owner review)
- [x] rules check before opening the reference (compliance.md §11); method frozen (commit 281fd85, config f6062fb0…)
- [x] `validation/`: safe extraction, checksums, comparison (extent IoU/precision/recall per confidence + own U-Net,
      buildings, roads, cut-off key breaks, reasons for misses), maps, `results.md` generated from metrics.json
- [x] results: precision 0.91–1.00 everywhere; Bidur IoU 0.78 / Phosretar 0.68; gorge recall ~0.30 (66–72 % of misses
      outside the terrain zone; trade-off documented, not re-tuned); 39/52 key breaks on EMS-damaged roads
- [x] robustness: Phosretar, Melamchi, Sikkim, Derna, Valencia, no-flood control: no crash, no failed stage
- [x] speed bug (Valencia damage 388 s → 31 s, identical results, equality test)
- [x] explainer `docs/explainers/M7_emsr927_robustness.md`

### M8 — Extras from the owner's original list (Oct 13) — DONE (awaiting owner review)
Rules checked before building (compliance.md §11): allowed inputs only, no new source/host/service.
- [x] helicopter landing-zone candidates (DEM slope, our flood map, pre-event OSM buildings; no helipad tags):
      Trishuli 172 of 215 likely cut-off settlements served, 43 none (steep gorge); "verify on site"
- [x] field package zip: GeoPackage + KML + GPX waypoints (tiers 1–3, landing zones, breaks, possible lakes)
      + sit-rep + README + attributions + ODbL; no new library
- [x] multi-date tracking: same track, same pre composite, same thresholds; receding / new ponding / possible
      lakes forming (persistence rule against wet snow/noise); Trishuli 4 dates, 3 possible lakes (verify)
- [x] sit-rep + dashboard integration; report ordered after all analysis stages
- [x] tests: landing zones, multi-date, e2e incl. extras; explainer `docs/explainers/M8_extras.md`
- (already built earlier: flood path M2/M3, rescue priority M4, confidence map M2, segmentation model M5)

### M9 — README, report, video, unseen-place testing, submit (Oct 13–14; target submit Oct 14 20:00 IST)
Owner decisions 2026-10-13: report laid out by the owner in Word/Google Docs from our draft;
video with the owner's voice AND captions; test on more than 10 unseen places; every
deliverable checked.
- [x] README: what it does, run from scratch, **any area + date** (live judging), dashboard, sit-rep, run times,
      limits, how to read results, results summary, AI model + reproduction, rules, attributions
- [x] report draft (`report/build_report.py` → HTML for Word): every number generated; ≤ 6 pages (owner lays out)
- [x] video kit (`report/video/`): timed script, captions (.srt), title/end cards; owner records
- [x] Devpost text field by field (`docs/devpost.md`) + 3:2 thumbnail and gallery (`report/devpost/`)
- [x] unseen-place campaign: 13 places + 8 invalid inputs (`docs/testing/unseen_places.md`); findings and fixes in
      `docs/explainers/M9_testing_and_submission.md`:
      huge-flood speed (subdivided flood polygons, prepared queries; identical results, tests),
      download retries for transient problems (tests), console without tracebacks (test),
      clear "no DEM data" message; documented: no S1 pair (Porto Alegre), false-alarm floor (Pokhara),
      deserts/flat cities (Dubai), sparse pre-event OSM (Swat, Chamoli)
- [x] later passes optional (owner decision): checkbox + "Track later passes" button + `--later-passes`;
      case study keeps them on (event file); dashboard uses each event's own settings
- [x] `doctor` checks Copernicus processing (tiny DEM request); leftover LLM settings removed
- [x] re-runs with the fixes: Thessaly, Kathmandu (+ landing-zone memory fix), invalid inputs (clear messages,
      no tracebacks); Trishuli re-run number by number: all identical
- [ ] final checks: fresh clone → hashed install → tests → pre-commit (gitleaks whole history) → doctor → live run;
      CI green; attributions + prototype label on every output; no local paths/secrets
- [ ] deliverables matrix (compliance.md §9) all ✅; Devpost text drafted; owner makes repo public + submits
- [x] display name **Sampark** (सम्पर्क, "connection"; owner decision 2026-10-13): README, report, video, Devpost,
      dashboard, field package (code stays `floodmap`)
- [x] AI-assistance attribution kept low-key (owner decision 2026-10-13, done 2026-10-14 before going public):
      co-author lines removed from commit messages (message-only rewrite: every commit's files and dates
      unchanged, backup kept locally); local assistant notes untracked; one honest "Acknowledgements" line
      in the README; frozen-method commit renamed 048c725 → 281fd85 (same files, config SHA-256 f6062fb0…)

## Approved proposals (from planning)
P1 numbers-by-construction copilot · P2 template sit-rep with LLM narrative + fallback ·
P3 HTML→PDF Devanagari · P4 segmentation model kept (M8) · P5 multi-date as full feature ·
P6 flood path in M2 · P7 cache key incl. config + version · P8 bridge approach logic ·
P9 buffered network + outside node + motorable/all-ways ·
P10 **Copernicus Data Space only** (owner decision 2026-10-11, replaces the earlier GEE plan) ·
P11 own basemaps, folium · P12 smoke runs every milestone + offline fixtures in CI ·
P13 `doctor` pre-flight · P14 hash-pinned deps + pip-audit · P15 no Earth Engine/geemap/osmnx ·
P16 VH also for decreases (M2) · P17 scene-shift correction — dropped (owner, 2026-10-11).
OSM access: stay on the brief's v1 URL via full history; ohsome v2 (new host + key) NOT used
(owner question 2026-10-12 "is it against the rules?" → stay literal).

## Owner actions
- [x] Set `git config user.email` to the GitHub noreply address (before the first commit)
- [ ] **Copernicus Data Space account** (free, dataspace.copernicus.eu), then `python -m floodmap login`
- [x] Create **private** GitHub repo (empty), send URL → https://github.com/itskiee/floodmap.git
- [x] ~~Post Discord questions~~ — Discord not found (owner, 2026-10-13): open questions decided conservatively
      by the PDF text, see `docs/compliance.md` §10
- [x] ~~Choose LLM provider~~ — not needed: no external AI (owner decision 2026-10-13)

## Discord questions (copy-paste)
1. Live evaluation format: our machine via screen share, a hosted URL, or judges cloning the repo? Time limit per run? Expected AOI size and date range?
2. May the Trishuli case study be pre-computed/cached, as long as the system can recompute it live?
3. Are display-only basemaps (OSM tiles / satellite basemaps) allowed in the dashboard if never used in the analysis?
4. For the segmentation model: are generic pretrained backbone weights (e.g. ImageNet) acceptable, or must the model be trained only on Kuro Siwo / Sen1Floods11?
5. "Nearest town or hospital": which facilities count (hospital only, or health posts/clinics)? Is "town" = OSM `place=town/city`? Do roads leaving the analysis area count as connected?
6. Which EMSR927 products should be compared (delineation vs grading, which AOIs)?
7. Do references/appendix count toward the 6-page report limit?
8. ohsome restricted the v1 snapshot endpoints in Aug 2026 (403). We rebuild the pre-event snapshot
   from the v1 full-history endpoint at the brief's URL (versions valid at the snapshot date). Is this
   acceptable, and if v1 is closed completely, may we use ohsome v2 (api.heigit.org, free API key)?
9. The datasets table lists OSM use as "buildings, roads, bridges". For the cut-off task ("settlements …
   nearest town or hospital") and the bonus ("list the settlements") we also use pre-event OSM place
   names (village/town) and hospitals/health facilities from the same ohsome snapshot. Please confirm
   this is fine.

(Dropped: Earth Engine question — we use only Copernicus Data Space, the source named in the
brief. "Which AI component is scored" — the brief says "choose one"; we declare the copilot.
Sentinel-2 cloud masks — we use the SCL band that is part of the Sentinel-2 L2A product.)
