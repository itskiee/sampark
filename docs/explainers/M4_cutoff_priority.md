# M4 explainer: who is cut off, and who comes first

Each part below covers **what**, **why**, **what if removed** and **what could replace it**.
All numbers were measured on 2026-10-12 (section 7). Data used: pre-event OSM (ohsome),
the M2 flood/debris map and the Copernicus DEM. Nothing else.

---

## 1. Question answered: "Who is cut off?"
The brief: *"Using the road network, find settlements that no longer have a road
connection to the nearest town or hospital."* Its example: after the event, villages B and
C lose access to the district hospital, while A and D keep it. Our unit test
`tests/test_cutoff.py` reproduces exactly this example: a broken bridge cuts off B and C,
A and D stay connected, and the break on B's old route is found at the bridge, 900 m away.

## 2. Settlements (proposal P18, approved)
- **Problem (measured in M3):**
  - 62 % of Trishuli's buildings are more than 1 km from any named OSM place.
  - Every affected building in Sikkim is more than 1 km from a named place.
  - Using only place points would ignore most people.
- **Method:**
  - Pre-event OSM buildings that are each within **75 m** of the next form one group
    (connected components with scipy).
  - A group of **at least 5 buildings** is a settlement.
  - A group wider than 3 km is split on a 1.5 km grid.

  Measured on Trishuli:

  | Link distance | Result |
  |---|---|
  | 50 m | 3,930 groups |
  | **75 m (used)** | **2,848 groups covering 91 % of buildings; largest 2.9 km across (a town)** |
  | 150 m | whole hillsides chain into 12 km "settlements" |

- **Naming:**
  - The group nearest to a named OSM place (within 1 km) takes its name.
  - Other groups within 3 km become "near <place>".
  - The rest get coordinates ("settlement at 27.8968°N 85.1911°E").
  - Unnamed OSM places never give names.
  - Named places with no mapped buildings are kept with building count 0, so an unmapped
    village is not silently dropped.
- **Population proxy:** the building count per settlement, as the brief requires.
- **Trishuli:**
  - 2,866 settlements;
  - 133 named directly, 2,190 "near <place>", 525 by coordinates, 18 named places without
    mapped buildings;
  - 97,640 buildings inside settlements, 9,451 isolated houses (still counted in totals).
- **If removed:** back to place points; most houses unassigned.
- **Alternative:** DBSCAN from scikit-learn does the same, but adds a dependency.

## 3. Road graph and reachability (`cutoff.py`)
- **Graph:**
  - Every pre-event OSM road way is split into pieces of at most **50 m**, so a settlement
    can join a long straight road anywhere and breaks are located to within 50 m.
  - Vertices shared by two ways (OSM junction nodes) connect them.
  - Roads that cross without a shared node (a bridge over a road) are correctly **not**
    joined. This is unit-tested.
- **Two networks (P9):**
  - **motorable**: what a 4x4/truck can use, `track` included (many hill villages only have
    tracks). This is the brief's "road connection".
  - **all ways**: also footpaths, steps and trails ("reachable on foot").
- **Outside node (P9):**
  - Roads and places are downloaded for the area plus 10 km.
  - A road continuing beyond that leads to the wider network.
  - So every node outside the downloaded area (less 200 m) joins a virtual *outside*
    node. Without it, every road at the edge would look like a dead end and half the map
    would be "cut off".
- **Destinations:**
  - nearest **hospital** (OSM hospitals);
  - nearest **town** (OSM place = town/city);
  - or the **outside** node.
  - The headline uses all three ("town or hospital"). The hospital set is also used alone,
    so we can say "cut off from <hospital>".
- **After the event:**
  - Road pieces touching detected flood/debris (10 m buffer) are removed.
  - Bridges flagged by the M3 bridge rule are removed too.
  - Both count **only at medium or high confidence** (see the ablation below).
  - Low-confidence breaks give a separate worst case: **"possibly cut off"**.
- **Algorithm:**
  - `scipy.sparse.csgraph.dijkstra` runs with *all destinations as sources at once*.
  - One pass gives every road node its nearest destination, the distance and the route
    back.
  - It runs before vs after, for each network and destination set.
  - For a cut-off settlement we walk its **pre-event route** and report the **first broken
    piece** on it: road name or bridge, distance and coordinates.
- **Status per settlement:**
  - cut off;
  - possibly cut off;
  - detour (still connected, but at least 1.5× and 2 km longer);
  - connected;
  - no access before the event (no mapped road within 500 m: trail-only village or OSM
    gap).
- **Sentences in the brief's form, never claiming certainty** (owner review 2026-10-12),
  for example:
  - *"near Bidur: likely cut off by road from Trishuli Hospital; road likely impassable at
    Pasang Lhamu Highway, 2.9 km away (flood/debris detected, high confidence). Still
    reachable on foot/trails. Needs field verification."*
  - *"near Bhainse: likely cut off by road from Trishuli Hospital; its own road (28A019R)
    lies in detected flood/debris (high confidence). No mapped footpath route avoids it
    either. Needs field verification."*
- **Key breaks:** for each broken road or bridge, how many settlements and buildings it
  cuts off. This tells repair crews which break reconnects the most people.
- **Dependencies:** none new. The plan said networkx, but scipy's sparse graph is about
  100× faster for this and was already installed.

## 4. Rescue priority (`stages/priority.py`)
Transparent tiers instead of an opaque weighted score, so every rank can be explained in
one sentence:

| Tier | Meaning | Why this order |
|---|---|---|
| 1 | cut off by road **and** buildings potentially affected | damage, and road teams cannot get there (air or foot access) |
| 2 | cut off by road, no damage detected | isolated: supplies, check-in |
| 3 | buildings potentially affected, still reachable by road | road teams can go |
| 4 | possibly cut off (low-confidence breaks only) or long detour | verify |

- **Order within a tier:**
  1. more potentially affected buildings, high confidence first;
  2. then more buildings in total (population proxy).
- **Reasons:** each entry carries its reasons (break, lost hospital, on foot, damage, on
  the flood corridor). The copilot (M5) will quote these instead of inventing reasons.
- **`priority.csv`:** opens in any spreadsheet, for field teams.

## 5. Flood-corridor tag (proposal P19, approved)
- **Method:** affected buildings, roads, bridges and settlements within 1 km of the traced
  flood path are tagged *on the flood corridor*, the most likely damage from **this**
  event. The rest is "elsewhere": other rivers or fields, to check separately.
- **Trishuli:**
  - 2,573 of 3,332 potentially affected buildings are on the corridor, 759 elsewhere;
  - 60 bridges on the corridor;
  - 78.9 km of the 80.5 km of affected motorable road.

## 6. Limitations (honest)
- **Settlement location:** a settlement joins the road network at the nearest road node
  within 500 m. On a hillside this can be a road across the river (OSM has no driveways).
- **"Possibly cut off" counts are large** (Trishuli 736). Low-confidence specks along long
  routes add up, so these are verification candidates, not alarms.
- **Settlements inside the flood area** count as cut off because their own road is under
  flood/debris. Their real problem is the damage itself, which the sentence states.
- **On foot:** only mapped paths count. People can also walk across open slopes, so "no
  mapped footpath route" is pessimistic.
- **Everything else is assumed intact:** OSM completeness limits everything. Unmapped
  roads or bridges are invisible, and road condition (paved, landslide-prone) is not
  modelled beyond the flood/debris map. Landslides away from the river are only partly
  detected (radar change, plausible-zone mask).
- **Undetected breaks:** a bridge washed out with no change visible within 150 m is missed.
  Radar revisit means images 2–10 days after the event.

## 7. Evidence (Trishuli, 1,791.7 km², full run; times from cache)
**Overall:**
- 2,866 settlements assessed; 25 hospitals, 7 towns, plus roads leaving the area.

**Cut-off results:**

| Network | Cut off | Possibly | Detour | Connected | No access before | Buildings cut off |
|---|---|---|---|---|---|---|
| motorable (by road) | **215** | 736 | 118 | 1,333 | 464 | **9,252** |
| all ways (on foot) | 108 | 202 | 217 | 2,133 | 206 | 3,695 |

**Key breaks:**

| Break | Location | Settlements cut off | Buildings cut off |
|---|---|---|---|
| Pasang Lhamu Highway | 85.337°E 28.163°N, by Syabrubesi | 47 | 2,093 |
| Pasang Lhamu Highway | 85.148°E 27.920°N | 8 | 1,556 |
| unnamed unclassified road | — | 4 | 830 |

**Priority:**
- tier 1: 86, tier 2: 129, tier 3: 96, tier 4: 799;
- the top 5 are all on the traced flood corridor.

**Timings:** osm 10 s (cached), damage 42 s → **22 s**, cutoff 35 s → **12.6 s** after
drawing all roads as one batch (matplotlib LineCollection) instead of 40,148 separate lines.
One sensitivity run took 2,208 s while the laptop had < 1 GB RAM free (Windows swapping);
the identical run took 57 s under the profiler. This is a reason to keep RAM free during
the live demo.

**Ablation: break confidence** (same run, `cutoff.break_min_confidence`)

| Roads break at | Settlements cut off by road | Buildings cut off |
|---|---|---|
| low (every detection) | 951 | 28,128 |
| **medium (default)** | **215** | **9,252** |
| high only | 189 | 7,648 |

Medium and high agree (215 vs 189), so the result is stable. Counting low-confidence
specks inflates it 4.4×. In the no-flood control those specks are exactly what appears
(below), which is why the default is medium.

### Robustness on events the system has never seen

| Run | Settlements | Cut off by road (buildings) | Possibly | Detour | On foot cut off |
|---|---|---|---|---|---|
| Melamchi 2021-06-15 | 736 | **42** (982) | 17 | 241 | 30 |
| Sikkim/Teesta 2023-10-04 | 27 | **18** (289) | 0 | 0 | 10 |
| No-flood control 2026-02-15 | 79 | **0** | 18 | 0 | 0 |

- No crash.
- First OSM downloads took 156–206 s this time; ohsome speed varies with server load.
- **No-flood control:** 0 cut off; all 18 "possibly" come from low-confidence specks.
- **Sikkim:** few OSM buildings (869), so few settlements.

## 8. Owner's questions (review 2026-10-12): answers for Q&A

### Q1. Why 75 m between houses, and not 150 m or another distance?
- **What the number does:** two houses closer than this belong to the same settlement. It
  decides what one "village" is.
- **Measured on Trishuli** (same flood map, only this setting changed):

  | Link distance | Settlements | Largest settlement | Likely cut off by road | Buildings in them |
  |---|---|---|---|---|
  | 50 m | 3,946 | 2.6 km | 276 | 8,008 |
  | **75 m (used)** | **2,866** | **2.9 km** | **215** | **9,252** |
  | 100 m | 1,934 | 7.8 km | 160 | 7,576 |
  | 150 m | 1,041 | 11.8 km | 106 | 9,588 |

- **Why not 150 m:**
  - Houses chain along roads and terraces, so whole hillsides and both sides of a valley
    become one "settlement" up to 11.8 km long.
  - Cut-off is decided at one point per settlement. Half of such a blob can be cut off
    while the other half is not, but it would get a single answer.
- **Why not 50 m:**
  - Himalayan houses are often 50–100 m apart, so real villages split into fragments.
  - 17 % of buildings would be left as isolated houses.
- **75 m is the knee:** 91 % of buildings belong to a settlement, while the largest stays
  town-sized (2.9 km).
- **What does not depend on it:** the number of *settlements* changes with the definition,
  but the number of *buildings* likely cut off (the brief's population proxy) stays between
  7,600 and 9,600. That is why reports lead with buildings.
- It is a config value (`osm.settlement_link_m`), chosen from these measurements, never
  tuned against EMSR927.

### Q2. What counts as the "outside world" or a destination?
- **The software defines it explicitly.** A settlement is connected if a mapped road reaches
  any of:
  1. a **hospital** from pre-event OSM (`amenity`/`healthcare` = hospital);
  2. a **town** from pre-event OSM (`place` = town or city);
  3. a road that **leaves the downloaded network area** (the analysis area plus 10 km, less
     a 200 m margin). Beyond that edge we have no OSM, so such a road is assumed to continue
     to the wider network, where more towns and hospitals exist.
- **It is not "leaving the flood zone".** A road that merely leaves the flood can end at a
  hamlet with no help. Destination 3 is 10 km beyond the whole analysis area.
- **Assumption:** roads in the 10 km buffer have no flood map, so they are assumed intact.
- **Measured effect on Trishuli:**
  - Only 25 of 2,402 reachable settlements have "a road leaving the area" as their nearest
    destination.
  - All 215 likely cut-off settlements had a real hospital or town as their destination
    before the event.
  - The hospital-only check gives the same 215.
  - The headline therefore does not rest on the outside assumption here.
- **Transparency:** every sentence names its destination ("from Trishuli Hospital" or "a
  road leaving the analysed area"). results.json `meaning.outside` states the assumption.
- **Discord question 5** asks the organizers whether roads leaving the area should count.

### Q3. Is a mapped road crossing flood/debris confirmed impassable?
- **No.** It is evidence, not confirmation. The output now says so in every sentence:
  "likely cut off", "road likely impassable … (flood/debris detected, high confidence)",
  "Needs field verification".
- **Why it is only likely:**
  - Radar sees *surface change* (water, wet mud, debris) on or within 10 m of the road. A
    road can be under shallow water and still passable, or already cleared.
  - Pixels are 20 m and a road is about 5 m wide. The detected patch may be beside the
    road, and OSM lines can be offset by metres.
  - Bridge decks sit above the river. The bridge rule uses nearby change, an indirect sign.
  - Timing: the radar image is 2–10 days after the event. Roads may have been cleared, or
    new landslides may have happened since.
  - The reverse error also exists: a road can be blocked without detection (a landslide
    above the flood zone, a collapse smaller than a pixel). "Connected" therefore says
    "no flood/debris detected … undetected damage is still possible".
- **How confidence helps:**
  - Each break carries the confidence of the detection.
  - Breaks count only at medium/high confidence. Low-only routes are "possibly cut off".
  - Ablation: low would give 951, medium 215, high 189.
- **What the output is for:** a *prioritised list of potential isolation* for field teams
  and helicopter crews to verify, not a statement of fact.
