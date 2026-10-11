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
- [ ] `check_cdse.py` DEM sample + `pytest -m cdse` green  ← **needs owner's CDSE login**
- [ ] CI green on GitHub  ← **needs owner's private GitHub repo**

**Done when:** offline tests + pre-commit green; doctor runs without printing secrets; skeleton
run writes results.json; CDSE inventory + DEM sample work; CI green.

### M1 — Sentinel-1 change detection on Trishuli (Oct 11)
- [ ] `s1_pairing`: same-track (relative orbit + pass) pre/post selection from the CDSE catalogue, slice merge, coverage ranking, clean failure
- [ ] `s1_change` (CDSE openEO): `sar_backscatter` with Copernicus DEM (`COPERNICUS_30`) terrain correction, speckle filter, pre-stack median, log-ratio VV/VH
- [ ] download as int16 GeoTIFF (local UTM, adaptive resolution, retries, job size limits) + cache
- [ ] `thresholds`: water-like decrease / debris increase / non-dark decrease; Otsu + bimodality check + fixed fallback; min mapping unit
- [ ] `viz`: full + focus PNGs, RGB change composite, attribution footer
- [ ] ablation PNGs (no speckle, single pre, VV vs VH, fixed vs Otsu)
- [ ] pairing smoke run on Melamchi 2021-06-15, Sikkim 2023-10-04, and a no-flood case
- [ ] unit tests (pairing, thresholds, fixtures for offline end-to-end)
- [ ] explainer `docs/explainers/M1_s1_change_detection.md`

Known Trishuli pairs (CDSE catalogue, 2026-10-11): track 85 ascending 16 Aug → 28 Aug;
track 121 descending 19 Aug → 31 Aug; track 19 descending 24 Aug → 5 Sep (all Sentinel-1D, VV+VH).

**Done when / GO-NO-GO:** `run --stages s1` prints pair + stats, second run is served from cache, and
the flood/debris path along the Bhote Koshi near Syabrubesi is clearly visible in the saved focus PNG.

### M2 — DEM masks, flood path, S2, confidence (Oct 11–12)
- [ ] slope, HAND (from GLO-30 via pysheds, locally), radar layover/shadow masks (local incidence angle)
- [ ] permanent water from pre-event S1/S2 (no JRC)
- [ ] flood-path tracing from any upstream point (official bonus; uses the same flow directions)
- [ ] S2 L2A from CDSE: SCL cloud mask, NDWI/MNDWI change, brightness change; graceful "too cloudy"
- [ ] asc + desc track fusion where available
- [ ] confidence layer high/medium/low (S1 / S2 / model agreement)

**Done when:** masked flood map PNG visibly cleaner than M1; flood path lists settlements downstream
(after M3); confidence raster produced; S2 absent → clear message, pipeline continues.

### M3 — Pre-event OSM + damage overlay (Oct 12)
- [ ] ohsome client: buildings, roads, bridges, place=*, hospitals/health facilities (snapshot rule)
- [ ] intersect with flood/debris + confidence → "potentially affected"
- [ ] bridge logic: deck/approaches buffer + crossing of a changed reach

**Done when:** affected-asset counts and layers for Trishuli; empty OSM → clear message.

### M4 — Cut-off analysis + priority ranking (Oct 12)
- [ ] networkx road graph (motorable vs all ways), buffered network area, virtual "outside" node
- [ ] before/after reachability to nearest town and hospital; broken segment + distance
- [ ] rescue priority ranking (building count, distance to reachable road, health access)

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
- [ ] helicopter landing-zone candidates
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
P13 `doctor` pre-flight · P14 hash-pinned deps + pip-audit · P15 no Earth Engine/geemap/osmnx.

## Owner actions
- [ ] Set `git config user.email` to the GitHub noreply address (before the first commit)
- [ ] **Copernicus Data Space account** (free, dataspace.copernicus.eu), then `python -m floodmap login`
- [ ] Create **private** GitHub repo (empty), send URL
- [ ] Post Discord questions (below)
- [ ] Choose LLM provider + create key with spend limit (by M5)

## Discord questions (copy-paste)
1. Live evaluation format: our machine via screen share, a hosted URL, or judges cloning the repo? Time limit per run? Expected AOI size and date range?
2. May the Trishuli case study be pre-computed/cached, as long as the system can recompute it live?
3. Are display-only basemaps (OSM tiles / satellite basemaps) allowed in the dashboard if never used in the analysis?
4. For the segmentation model: are generic pretrained backbone weights (e.g. ImageNet) acceptable, or must the model be trained only on Kuro Siwo / Sen1Floods11?
5. "Nearest town or hospital": which facilities count (hospital only, or health posts/clinics)? Is "town" = OSM `place=town/city`? Do roads leaving the analysis area count as connected?
6. Which EMSR927 products should be compared (delineation vs grading, which AOIs)?
7. Do references/appendix count toward the 6-page report limit?

(Dropped: Earth Engine question — we use only Copernicus Data Space, the source named in the
brief. "Which AI component is scored" — the brief says "choose one"; we declare the copilot.
Sentinel-2 cloud masks — we use the SCL band that is part of the Sentinel-2 L2A product.)
