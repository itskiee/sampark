# M2 explainer — terrain masks, two radar tracks, Sentinel-2, confidence, flood path

For each part: **what**, **why**, **what if removed**, **what could replace it**.
All numbers were measured on 2026-10-11/12 (section 9).

---

## 1. Why M1 alone was not enough
M1 compared radar images pixel by pixel. That finds the flood corridor, but also flags
changes on mountain slopes (vegetation, moisture, radar geometry effects). M2 adds physical
knowledge from the Copernicus DEM: *where can a flood or debris deposit actually be?*
It also adds independent second opinions (the other radar track, Sentinel-2), so every
mapped area carries a confidence level.

## 2. Terrain stage (`terrain.py`, `stages/terrain.py`)
All derived from the **Copernicus DEM GLO-30**, fetched from Copernicus Data Space for the
area plus a 10 km margin (so rivers entering the area have some upstream catchment).

| Layer | How | Why |
|---|---|---|
| **Flow direction** | pysheds: fill pits → fill depressions → resolve flats → D8 | water flows to the steepest-descent neighbour; filling avoids false sinks in DEM noise |
| **Flow accumulation** | number of cells draining through each cell | rivers are cells with large upstream area |
| **Streams** | upstream area ≥ 5 km² | keeps rivers and torrents, not every gully |
| **HAND** | height above the stream each cell drains into | floods/debris settle in low ground near rivers |
| **Slope** | DEM gradient | water does not stand and thick deposits do not stay on steep slopes |
| **Layover/shadow** | slope along the radar look direction vs incidence angle | radar data there is unreliable |
| **Permanent water** | dark in the BEFORE images (VV ≤ −18 dB, VH ≤ −24 dB) in the plausible zone | the normal river is not "flooded" |

- **Plausible zone** = HAND ≤ 30 m **and** slope ≤ 30°. Glacial-lake/debris floods in gorges
  reach 10–20 m depth and deposit on terraces, so 30 m is generous. Normal river floods
  would use ~10–15 m.
- **Layover/shadow rule:** s = elevation change per metre moving away from the satellite.
  Layover where atan(s) > incidence angle (the slope faces the radar more steeply than the
  beam). Shadow where atan(−s) > 90° − incidence (the slope faces away and is never lit).
  Look direction: Sentinel-1 is right-looking, ascending ≈ 77° (east-north-east),
  descending ≈ 283°. Incidence angle from the catalogue (fallback 39°). Shadows cast by
  distant ridges are not modelled (limitation).
- **If removed:** the slope noise returns (compare `outputs/*/s1/s1_overview.png` with
  `confidence/flood_map.png`).
- **Alternatives:** ready-made HAND products (MERIT Hydro, global HAND) are **forbidden**
  (not allowed inputs). This is why we compute it ourselves from the Copernicus DEM.
- **Engineering note:** pysheds 0.5 still calls `numpy.in1d`, removed in numpy 2.4+. We add
  it back as an alias of `numpy.isin` (identical for its 1-D use) instead of pinning an old
  numpy. A unit test runs real pysheds on a synthetic valley (stream on the floor, HAND ≈ 0
  there, > 300 m on the ridges, flow downhill), so this would be caught immediately.

## 3. Two radar tracks
- The `s1` stage processes the best pair **and** the best pair from the opposite pass
  direction. They view the valley from opposite sides: a slope in shadow for one is lit for
  the other.
- Trishuli: track 85 ascending (after 28 Aug) + track 19 descending (after 5 Sep). Track
  121 descending (after 31 Aug) was not chosen because it covers less of the area, and
  coverage ranks first.
- Each track uses its **own** layover/shadow mask.

## 4. Sentinel-2 (`s2.py`, `stages/s2.py`)
- Clearest "before" day (≤ 60 days earlier) and earliest acceptable "after" day (≤ 30 days
  later) from the CDSE catalogue; one openEO request for both days.
- Clouds, shadows and snow are removed with the **SCL band** (part of the official L2A
  product, so no extra dataset).
- New water: MNDWI = (green − SWIR)/(green + SWIR) > 0 after, not before.
  Mud/debris: NDVI dropped ≥ 0.25 and ≤ 0.35 afterwards (vegetation stripped).
- **Never required:** too cloudy → stage skipped with a clear message; radar results unchanged.
- Trishuli: 12 Aug → **27 Aug (1 day after)**, 31 % of the area clear in both.
  *Caution:* partly cloudy optical data can add false detections near cloud edges.

## 5. Confidence (`flood_map.py`, `stages/confidence.py`)
- A detection only counts inside the plausible zone, outside permanent water, and where
  that source has a valid observation (radar: no layover/shadow; optical: clear sky).
- Class priority when sources disagree: water > wet mud/debris > rough debris (the more
  hazardous reading wins).
- **high** = at least 2 independent sources agree (within 1 pixel, tolerating small
  misregistration); **medium** = 1 source, patch ≥ 5,000 m²; **low** = 1 source, small patch.
- Every area is labelled **"potentially affected"**, never "destroyed".
- Outputs: `flood_map.tif` (class, confidence, number of agreeing sources),
  `flood_areas.geojson` (polygons for the damage overlay in M3 and the dashboard),
  `flood_map.png` over a hillshade made from the Copernicus DEM (our own basemap: no
  external map tiles).

## 6. Flood path (official bonus) (`stages/flood_path.py`)
- From an upstream point, snap to the channel (largest upstream area within 300 m), then
  follow the D8 flow directions downstream to the edge of the DEM.
- Outputs: path GeoJSON, km markers with elevation, length, elevation drop, share of the
  path crossing detected change. Settlements along it are added in M3 (pre-event OSM).
- Trishuli: **81.9 km**, **1,318 m** elevation drop, **50 %** of the path crosses detected
  flood/debris change. The traced path lies on top of the mapped corridor.

## 7. Testing
- Unit tests: slope, layover/shadow geometry, flow tracing (snap, loops, sinks), real
  pysheds on a synthetic valley, source combination (agreement, masking, permanent water,
  layover per track), Sentinel-2 date choice and classes.
- **Offline end-to-end test**: the whole pipeline (s1 → s2 → terrain → confidence → flood
  path, cache included) on a synthetic valley, with CDSE replaced by local fakes. CI tests
  the complete pipeline with no credentials.

## 8. Limitations (honest)
- Wide flat valley floors (farmland) have low HAND. Monsoon rice paddies fill with water
  and can look like new water; EMSR927 (M7) will measure this.
- Partly cloudy Sentinel-2 can raise confidence wrongly near cloud edges.
- 30 m DEM: narrow gorges and small terraces are smoothed. Passive (ridge-cast) radar
  shadow is not modelled.
- The flood path follows DEM flow directions. Avulsions (rivers jumping channel in a debris
  flow) and backwater (a debris dam) are not modelled.

## 9. Evidence (Trishuli, 1,791.7 km²)
- Terrain: plausible zone 82.56 km² (4.6 % of the area); permanent water 0.385 km².
- Confidence: **15.60 km² potentially affected**: water-like 3.86, wet mud/debris 9.22,
  rough debris 2.52 km². By confidence: **high 10.46**, medium 2.41, low 2.72 km².
  3 sources (S1 85 asc, S1 19 desc, S2).
- Flood path: 81.9 km, 1,318 m drop, 50 % crossing detected change.
- Timings: first full M2 run 383 s (second track + DEM + Sentinel-2 downloads); cached
  re-run ≈ 65 s.
- Tests: 180 offline tests pass, including the offline end-to-end pipeline test.

### Robustness on events the system has never seen (full M2 pipeline)
| Run | "Debris" before masks (M1) | Potentially affected after M2 | Notes |
|---|---|---|---|
| Melamchi 2021-06-15 (~180 km²) | 21.33 km² | **3.57 km²** (high 2.60, medium 0.62, low 0.36) | S2 45 % clear, used; river, confluence and widened channel mapped |
| Sikkim/Teesta 2023-10-04 (~280 km²) | 12.16 km² | **2.06 km²** (high 1.42) | S2 only 2 % clear → skipped with message |
| Trishuli dry season 2026-02-15, no flood | 0.70 km² | **0.09 km²** | control stays essentially empty |
- No crash in any run. Times 147–196 s each (first run, including the DEM, the second track
  and Sentinel-2).
- **P17 (scene-shift correction) re-assessed:** the Melamchi monsoon brightening that
  motivated it is now removed by the terrain masks (21.3 → 3.6 km², and the remaining
  detections sit on the river). P17 adds complexity for little gain → **dropped (owner
  decision 2026-10-11)**.
