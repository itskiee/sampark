# M3 explainer: pre-event OpenStreetMap and damage overlay

Each part below covers **what**, **why**, **what if removed** and **what could replace it**.
All numbers were measured on 2026-10-12 (section 8).

---

## 1. Question answered: "What was damaged?"
The brief asks us to overlay OpenStreetMap buildings, roads and bridges and estimate which
were hit. M3 adds:
- the `osm` stage: pre-event OSM from the ohsome API;
- the `damage` stage: OSM overlaid with the M2 confidence map;
- settlements along the traced flood path (the official bonus asks us to list them).

## 2. Getting OSM "as it was before the event" (`ohsome_client.py`)
**Rule:** only OSM from before the event, and only from the ohsome API, the source the
brief names. Edits made after the event lead to disqualification.

**What changed in 2026 (found while building M3):**
- ohsome blocked the v1 *snapshot* download endpoints (`/elements/geometry` and
  `/elements/centroid` return HTTP 403). The maintainers say why in
  [ohsome-api issue #346](https://github.com/GIScience/ohsome-api/issues/346), 24 Aug 2026.
- Their replacement, v2, sits on a different host and needs an API key.
- Counting endpoints and the v1 **full-history** endpoint still work.

**How we get the snapshot, on the exact URL the brief names:**
- `/elementsFullHistory/geometry` returns *every version* of every OSM object in a time
  window, each with `@validFrom` / `@validTo`.
- We ask for a 24 h window **ending at the snapshot time**, then keep only the versions
  still valid at that moment (`@validTo == snapshot`).
- By definition, that is OSM exactly as it was at the snapshot.

**Proof (live, 2026-10-12):** our reconstruction compared with ohsome's own snapshot count
at the same timestamp:

| Layer | ohsome official count | Our reconstruction |
|---|---|---|
| Buildings, whole Trishuli area | 107,136 | 107,136 |
| All roads/paths, whole Trishuli area | 7,417 | 7,417 (6 older versions dropped correctly) |
| Settlements (place points) | 196 | 196 |

`pytest -m network` repeats this check (`tests/test_ohsome_live.py`).

**Runtime guard:** if any kept version starts or ends after the snapshot, the code raises
`DataRuleViolation`. Wrong-window bugs cannot leak post-event edits silently. Unit tests
cover this (`tests/test_ohsome_client.py`).

**Snapshot time:**
- Rule: `min(00:00 UTC on the day before the event, newest ohsome data)`.
- Trishuli: **27 Jul 2026 09:00Z**, the same date the brief names.
- Older events automatically get their own historical OSM, for example Melamchi 2021 →
  14 Jun 2021.

**Alternatives considered:**
- **ohsome v2:** new host plus API key. Not used, because it steps away from the URL the
  brief names (owner decision: no risk).
- **Overpass / osmnx downloads:** forbidden, because they return *current* OSM, which
  contains post-event edits.
- **Geofabrik history files:** not named in the brief, and too large.

**If removed:** no damage, cut-off or flood-path settlement answers.

## 3. What we download (`osm.py`)

| Layer | ohsome filter (short) | Area |
|---|---|---|
| Buildings | `building=*` polygons | analysis area |
| Roads & paths | every `highway` way except construction/proposed/abandoned/platforms | area + 10 km |
| Places | `place = city, town, village, hamlet, isolated_dwelling` | area + 10 km |
| Health facilities | `amenity = hospital/clinic/doctors`, `healthcare = …` | area + 10 km |

- **Only what the brief names** (owner rule 2026-10-12: nothing against or beyond the
  authorities' rules): the brief's OSM row lists buildings, roads and bridges, and its task
  text needs settlements, the nearest town and hospitals. Nothing else is downloaded.
  Places and hospitals: the data rule allows "OpenStreetMap as it was before the event"
  without restricting features, and the task cannot be done without them. Owner decision
  2026-10-12: keep them; Discord question 9 asks for written confirmation.
- **Licence:** every OSM-derived output folder gets `LICENSE_OSM_DATA.txt` (ODbL), and
  results.json carries `data_licenses`, because the brief says published modified OSM data
  must stay under ODbL. OSM
  helipads were considered for the landing-zone extra and **removed**; landing zones (M8)
  will use the Copernicus DEM and Sentinel data plus these OSM layers.
- **10 km margin:** the nearest town or hospital can lie outside the area, and roads that
  leave the area must not look like dead ends (cut-off analysis, M4).
- **Bridges** are road ways with a `bridge` tag, footbridges included. In Nepal,
  suspension footbridges are often a village's only link.
- **Motorable roads** (configurable): motorway … tertiary, unclassified, residential,
  service, `track`. Tracks count because many hill villages are reached only by unpaved
  tracks. Paths/footways are kept for the "all ways" variant in M4.
- **Untrusted text:** OSM is user-edited. Only a fixed list of tags is kept, and
  control/bidi characters are stripped and lengths capped. These names later reach the
  dashboard and the language model (prompt-injection defence).
- **No population tag:** the OSM `population` tag is never kept. The brief's population
  proxy is the count of pre-event OSM buildings, nothing else.

**Population proxy per settlement:**
- Each building is assigned to the nearest named place within 1 km.
- Places outside the analysis area show "unknown", not 0, because their buildings were not
  downloaded.

**Speed (measured):**
- Each ohsome request has a fixed cost of roughly 15–35 s, so fewer requests are faster.
- With 10 tiles, the first download took **737 s**.
- With per-layer tiling (buildings in tiles of up to 1,000 km², sparse layers in one
  request) it took **109 s**, with identical counts.
- Re-runs come from the cache (about 14 s).

## 4. Damage overlay (`damage.py`, `stages/damage.py`)
- **Rule:** an asset is **potentially affected** when it touches an M2 flood/debris
  polygon. It inherits that polygon's class and confidence. If several polygons touch it,
  the highest confidence and the most hazardous class win (water > wet mud > rough debris),
  the same rule as M2.
- **Buffers:** 10 m for buildings and roads, 20 m for health facilities. The radar pixel is
  20 m, and OSM and the satellite grid can be offset by a few metres.

**Bridges (P8):**
- The river under a bridge was already water before the event, so a destroyed bridge often
  shows *no change on the deck itself*.
- **Deck/approach:** flood or debris within 30 m of the bridge line.
- **River reach:** flood or debris within 150 m, meaning a changed reach just up- or
  downstream.

**Roads:**
- We report the length of each way inside detected areas (motorable vs all ways), plus the
  affected pieces as geometry.
- M4 removes those pieces from the road graph to find who is cut off.

**Settlements:** potentially affected buildings per place, and the share of the place's
buildings.

**Map** (`damage_map.png` and focus window), drawn over our own DEM hillshade:
- affected buildings in red;
- roads, with affected parts in red;
- bridge triangles coloured by evidence;
- hospitals;
- labels for the most affected settlements.

**Wording:** everything is labelled "potentially affected (estimate; verify on the
ground)", never "destroyed". Change detection cannot tell a damaged house from a flooded
field next to it.

## 5. Settlements along the flood path (official bonus)
- Pre-event OSM places within 1 km of the traced path, in downstream order.
- For each one:
  - km from the start;
  - distance from the path;
  - **height above the river** at that point, from the Copernicus DEM;
  - building count.
- **Low-lying** settlements (≤ 30 m above the river, the same limit as the M2 flood zone)
  are marked. A village within 1 km horizontally can sit 500 m up the slope and be safe.
  The height tells rescuers which ones matter.
- Trishuli: start point moved to where the Bhote Koshi enters the area, the largest
  drainage line in our DEM at 28.30°N, upstream of Rasuwa Gadhi. The path is **87.8 km**
  long, drops **1,688 m** and passes **43 settlements, 9 of them low-lying**: Resuo +14 m,
  Rasuwa Gadhi +16 m, Mailung +28 m, Betrawati +29 m, Bhainse +21 m, Devighat +18 m, …

## 6. Engineering
- **Optional stage inputs** (`uses=` in `register_stage`): `--stages damage` now pulls in
  Sentinel-2 automatically. Without this, it gave 12.29 km² from 2 sources instead of the
  full run's 15.60 km² from 3 sources. Bug found during the M3 run, test added.
- **Legend placement:** map legends now sit below the map, so they never hide data.
- **`doctor`:** new check "ohsome osm download" performs a tiny real snapshot download, so
  we know before a demo if ohsome blocks the full-history endpoint too.
- **Tests:**
  - ohsome client: window, validity filter, post-snapshot guard, tiling, de-duplication;
  - OSM layers: text cleaning, no population tag, footprint repair, motorable/bridge
    flags, POI categories, building counts;
  - damage rules: confidence/class priority, buffers, road length, bridge evidence, empty
    inputs;
  - the offline end-to-end test now includes a synthetic village, road, bridge and
    hospital (CI tests osm → damage → flood-path settlements with no network).

## 7. Limitations (honest)
- **Validation:** "potentially affected" is detected change touching an asset, not a
  damage assessment. EMSR927 (M7) will measure agreement.
- **Lower-valley share:** 79 % of the flagged buildings (2,644 of 3,332) are in the wide
  lower valley south of 28.0°N (Betrawati, Bidur, Devighat, the Tadi tributary). Only 688
  are in the upper gorge where the debris flood started. This may be real, or partly
  monsoon effects on paddy fields (wet-mud class 69 % there). We do not know until the M7
  comparison, and we will not tune thresholds against EMSR927.
- **Other causes:** the system maps *all* change, including rivers not hit by the event.
- **OSM completeness:** an unmapped house cannot be counted. In Trishuli, **62 %** of
  buildings (66,124) are more than 1 km from any named place, because rural houses are
  scattered and place points are sparse. These buildings are counted in totals but not
  under a settlement name (see proposal P18).
- **Snapshot age:** OSM is from 27 Jul 2026, a month before the event. Buildings built in
  between are missing.
- **Service dependency:** ohsome may also restrict the full-history endpoint. Mitigations:
  cache, a pre-warmed case study and the `doctor` check.

## 8. Evidence (Trishuli, 1,791.7 km², full run)
- **OSM:** 107,091 buildings, 3,921 km of roads/paths in the area (2,289 km motorable),
  217 bridges, 492 places, 25 hospitals, 21 other health facilities.
- **Damage (potentially affected):**
  - **3,332 buildings** (high 2,524, medium 255, low 553);
  - **80.5 km motorable road** (105.2 km all ways);
  - **163 bridges** (124 deck/approach, 39 river reach);
  - 0 health facilities.
- **Most affected settlements** (buildings potentially affected / buildings): Betrawati
  304, Naubisephat 284, Bhainse 274, Devighat 231, Rasuwa Gadhi 61, Bidur 59.
- **Timings:** first OSM download 109 s; full cached pipeline 114 s.

**Ablation (same run):**

| Setting | Buildings potentially affected | High confidence |
|---|---|---|
| no buffer (0 m) | 2,638 | 2,201 |
| **10 m (default)** | **3,332** | **2,524** |
| 20 m | 4,039 | 2,811 |

| Bridge rule (P8) | Bridges flagged |
|---|---|
| deck only (0 m) | 86 |
| + approaches (30 m) | 124 |
| + changed reach (150 m, default) | 163 |

### Robustness on events the system has never seen
(full pipeline incl. osm + damage; each with its own historical OSM snapshot)

| Run | OSM snapshot | Buildings in area | Potentially affected (high / med / low) | Bridges flagged (medium+high) | Most affected settlement |
|---|---|---|---|---|---|
| Melamchi 2021-06-15 | 2021-06-14 | 21,472 | **574** (267 / 147 / 160) | 38 (31) | Melamchi 51 |
| Sikkim/Teesta 2023-10-04 | 2023-10-03 | 869 | **304** (274 / 28 / 2) | 12 (11) | none named within 1 km (see P18) |
| No-flood control 2026-02-15 | 2026-02-14 | 2,903 | **26** (0 / 0 / 26) | 10 (1) | — |

- No crash. First OSM download 45–113 s per area; damage 2–10 s.
- **Melamchi:** the most affected named settlement is Melamchi itself, the town the 2021
  event is named after. This is a plausibility check only, not a validation.
- **No-flood control:** **zero** high- or medium-confidence buildings. The 26 low-confidence
  buildings and 9 of the 10 flagged bridges come from single-source specks. That is what
  the confidence levels are for. **Consequence for M4:** a road or bridge should count as
  *broken* only at medium/high confidence; low confidence is shown as "possible".
- **Sikkim:** 304 potentially affected buildings, but none within 1 km of a named OSM
  place. Settlement naming needs building clusters (P18).
- **Console bug found and fixed:** when output went through a pipe on Windows (cp1252),
  printing "→" crashed the run summary *after* the pipeline had finished. The CLI now
  always writes UTF-8 and replaces unprintable characters. Regression test added.

## 9. Proposals for the owner (need approval)
- **P18: settlements from building clusters.**
  - *Problem:* 62 % of Trishuli buildings, and all affected Sikkim buildings, are more
    than 1 km from a named place.
  - *Method:* group buildings that are within ~150 m of each other (graph connected
    components with scipy, no new dependency). Each group of at least ~5 buildings is a
    settlement, named after the nearest OSM place ("near Betrawati") or by coordinates.
  - *Data rules:* OSM buildings and places only, so no change.
  - *Scoring:* cut-off 20 % (every inhabited cluster gets a cut-off answer, not only named
    villages) and damage mapping 30 %.
  - *Timing:* built at the start of M4, which needs settlements anyway.
- **P19: "on the flood corridor" tag.**
  - *Method:* when a flood path is traced, mark every affected asset as on or off the
    corridor (within the low-lying zone along the path).
  - *Why:* rescuers can tell apart damage from *this* event and change elsewhere, such as
    another river or paddy fields. In Trishuli this would separate the Bhote Koshi–Trishuli
    corridor from the Tadi tributary.
  - *Data rules:* none.
  - *Scoring:* mapping quality, plus honesty in Q&A.
