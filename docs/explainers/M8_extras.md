# M8 explainer: extras from the owner's original list

The owner asked for extras that make the system more useful to the people who use it, built
only after the core requirements were complete and checked (M1–M7). Already built earlier:
- flood-path bonus (M2/M3);
- rescue priority (M4);
- confidence map (M2);
- our own segmentation model (M5).

M8 adds **helicopter landing-zone candidates**, **field exports** and **multi-date
tracking**.

**Rules check (before building, `docs/compliance.md` §11):** every extra uses only allowed
inputs (Sentinel-1/2 from Copernicus Data Space, the Copernicus DEM, pre-event OSM) and our
own results. No new data source, web host or outside service. No OSM helipad tags.

---

## 1. Helicopter landing-zone candidates (`landing_zones.py`, stage `landing_zones`)
- **For whom:** army/police helicopter crews and district emergency centres, for settlements
  likely cut off by road.
- **Method:**
  1. Suitable ground: slope ≤ 6° (Copernicus DEM), no detected flood/debris, no water,
     observed by the radar, at least 30 m from pre-event OSM buildings.
  2. A 60 m × 60 m clear square must fit (binary erosion). Each such patch gives one
     candidate at its most interior point.
  3. For each likely-cut-off settlement: up to 2 nearest candidates within 3 km, with
     distance, height difference, size, slope, and whether the straight walk crosses
     detected flood/debris. Walks that avoid it come first.
- **Why these choices:**
  - Near-level, open, dry ground is the minimum for a landing.
  - The walking distance limit keeps the candidate usable for the settlement's people.
  - Every threshold is a config value with its reason.
- **Honesty:** trees, power lines, ground firmness and the approach cannot be seen in
  20–30 m data. Every candidate says "candidate only: verify … on site". If no flat ground
  exists within 3 km, the system says so rather than inventing a spot.
- **Trishuli** (measured): 747 candidate sites in the area. **172 of the 215 likely-cut-off
  settlements** have a candidate within 3 km. **43 have none**, mostly in the steep upper
  gorge, which is important information for crews.
- **If removed:** crews would have to find landing sites from maps by hand.
- **Alternative:** OSM helipad tags were rejected, because they go beyond the brief's OSM
  content.

## 2. Field exports (`exports.py`, stage `exports`)
- **What:** one zip per run, `exports/floodmap_<run>_field_package.zip`, usable offline:
  - `layers.gpkg`: every layer in one GeoPackage (QGIS/ArcGIS desks).
  - `kml/*.kml`: the same layers for Google Earth and phone map apps, with readable names
    and descriptions (for example the cut-off sentence). Flood polygons are simplified by
    5 m, far below the 20 m analysis resolution, to keep files small.
  - `waypoints.gpx`: handheld GPS waypoints:
    - PRIORITY settlements (tiers 1–3 only, because GPS units hold a limited number of
      waypoints);
    - LZ-CANDIDATE landing zones;
    - BREAK road/bridge breaks;
    - POSSIBLE-LAKE water bodies.
  - The sit-rep (PDF + text), a README explaining every file, the attributions and the
    ODbL notice.
- **No new library:** the existing GDAL writes GeoPackage, KML and GPX.
- **Trishuli** (measured): 2.8 MB, 10 layers. The GPX holds about 700 waypoints after the
  tier filter (1,492 before).
- **Bug caught:** GeoPackage field names are case-insensitive, so OSM `name` clashed with the
  added `Name`. Fixed by renaming OSM fields to `osm_name`. A second bug, list-valued fields
  from GeoJSON, was fixed by writing them as text.

## 3. Multi-date tracking (`multidate.py`, stage `multidate`)
- **Purpose:** after the first post-event image, is the water receding? Is new water ponding?
  Is a lake forming behind debris, a known secondary hazard after debris floods?
- **Method:**
  1. Later Sentinel-1 passes on the **same track** (relative orbit + pass) up to 40 days
     after the event, at most 3, each downloaded once from Copernicus Data Space and cached.
  2. Each is compared with the **same pre-event composite** and classified with the **same
     frozen thresholds** as the first image, inside the terrain-plausible zone.
  3. Outputs: area per date; receding / new / persistent water between the first and last
     date; growing water bodies.
- **Possible lake rule** (all config values): a water body on the last date of at least
  0.02 km², at least 1.5 × its area on the first date, and present (≥ 10 % of its final area)
  on **at least two later dates**.
  - The persistence rule was added after the Trishuli run showed water bodies appearing on
    the **last image only** at the high northern edge in October, for example 0.23 km² from
    nothing. Wet snow and noise look as dark as water to radar.
  - A first version counted a single stray pixel as "present"; this was tightened to 10 % of
    the final area. Tests cover both cases.
  - The rule comes from that physical failure mode, not from any reference map.
- **Honesty:** flagged bodies are "possible lake forming: verify (e.g. a debris dam); not
  confirmed".
  - Later images are further from the pre-event season, so the *debris* classes drift
    upwards over time (vegetation and soil-moisture change). Water is the robust signal;
    later-date debris numbers need caution.
  - If no later pass exists yet (for example in a live demo right after an event), the stage
    says so and is skipped.
- **Trishuli** (measured): track 85 ascending, 4 dates (28 Aug, 9 Sep, 21 Sep, 3 Oct).
  - Water 0.65 → 1.02 km²; receding 0.53 km²; new ponding 0.90 km²; persistent 0.13 km².
  - **3 possible lakes forming**, the largest growing 0 → 0 → 0.030 → 0.064 km² at 28.081 N
    85.407 E.
  - Wet mud/debris class 5.1 → 10.4 km², the seasonal drift noted above.
  - First run about 20 min (three downloads), then 20 s from the cache.

## 4. Integration
- The sit-rep has a line for landing zones and later passes. Stage order was fixed so the
  report comes after every analysis stage.
- Dashboard tabs: *Landing zones*, *Later passes*.
- The field package includes every layer.
- The offline end-to-end test now also runs landing zones, the sit-rep and the field package
  (checks the zip contents).
- Unit tests: landing zones (3), multi-date (3), plus the existing suite. 231 offline tests
  pass.
