# PLAN — floodmap (Track B: Mapping Flood Damage from Space)

Deadline: Devpost **Oct 15 2026, 00:15 IST** (end of Oct 14). Full scope, nothing cut.
Order of work is core first (M0–M7), then extras (M8), so the system is always demo-able.
Rule hierarchy and working rules: see [CLAUDE.md](CLAUDE.md).

**Data sources (owner decision 2026-10-11: follow the brief literally, no risk):** Sentinel-1,
Sentinel-2 and Copernicus DEM only from the **Copernicus Data Space Ecosystem** (named in the
brief); OpenStreetMap only from the **ohsome API** (named in the brief). No Google Earth Engine.

## Milestones

### M0 — Setup (Oct 10–11)
- [x] git repo, `.gitignore`, `.gitattributes`, `.python-version`, venv (Python 3.12)
- [x] dependencies pinned with SHA-256 hashes (`requirements*.txt` via `uv pip compile --universal`)
- [x] CLAUDE.md, PLAN.md, README skeleton, SECURITY.md, LICENSE, `.env.example`
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

### M5 — Copilot (EN + NE) + validator + sit-rep (Oct 13)
- [ ] numbers-by-construction slots filled from results.json; Devanagari digit formatting
- [ ] validator (numbers + place names vs JSON), adversarial tests, "not in our data" path
- [ ] one-page sit-rep: deterministic template + LLM narrative; template-only fallback
- [ ] Nepali PDF with correct Devanagari shaping

### M6 — Dashboard end-to-end (Oct 13)
- [ ] Streamlit: AOI draw/enter + date → run → layers, tables, copilot chat, sit-rep download
- [ ] progress messages, attribution footer, prototype disclaimer; own basemaps (hillshade/S1/S2)

### M7 — EMSR927 comparison + robustness (Oct 14)
- [ ] `validation/`: IoU / precision / recall, affected-building agreement, disagreement analysis
- [ ] 4+ unseen AOIs/dates (Himalayan + non-Himalayan); fix every crash; `validation/results.md`

### M8 — Extras (Oct 12–14, after core works)
- [ ] helicopter landing-zone candidates (from Copernicus DEM slope/flatness + Sentinel + allowed OSM layers only; no OSM helipad tags)
- [ ] field exports: GeoJSON + KML of all layers; EN + NE PDF sit-rep
- [ ] multi-date tracking (receding water / new ponding / debris-dammed lake flag)
- [ ] segmentation model (Sen1Floods11 / Kuro Siwo subset), honest metrics on unseen Himalayan scenes
- [ ] confidence map summary in dashboard + report

### M9 — README, report, video, submit (Oct 14)
- [ ] README run-from-scratch guide; report ≤6 pages incl. limitations; 3-min video script
- [ ] final checks (fresh clone, doctor, tests, attributions), make repo public, submit

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
- [ ] Post Discord questions (ready to paste: `docs/discord_questions.md`, 1,376 characters)
- [ ] Choose LLM provider + create key with spend limit (by M5)

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
