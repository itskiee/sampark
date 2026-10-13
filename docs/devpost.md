# Devpost submission: what to paste in each field

Numbers come from README.md / validation/results.md (case-study run `20261013T103517Z_6d540fab`).
Paste field by field.

---

## Step 2: Project overview

**Project name** (56 of 60 characters):
```
Sampark: who is cut off after a flood, mapped from space
```

**Elevator pitch** (190 of 200 characters):
```
Give it an area and a flood date. Using free Sentinel radar, our own AI flood model and pre-event OpenStreetMap, Sampark maps where the flood hit, what was damaged and who is likely cut off.
```

**Thumbnail** (Edit thumbnail): `report/devpost/thumbnail.png` (3:2, our own map).

---

## Step 3: Project details

**About the project** (Markdown; copy everything between the two lines):

---------- copy from here ----------

## Inspiration
After a Himalayan debris flood, the first hours decide who gets help. District emergency centres,
helicopter crews and relief teams need three answers before anyone can reach the valley: where did
the flood hit, what was damaged, and which villages lost their road. Free Sentinel satellites see
every valley every few days, but turning raw radar into those answers takes specialists. We wanted
a tool that does it for any area and date, explains every result, and never pretends to be certain.

## What it does
Give Sampark (सम्पर्क, Hindi and Nepali for "connection") an area and a flood date. From Sentinel-1 radar, Sentinel-2 (when clear), the
Copernicus DEM and OpenStreetMap as it was before the event, it produces:
- a **flood and debris map** with a confidence level for every area;
- **potentially affected buildings, roads, bridges and health facilities**;
- **who is cut off**: every settlement routed to the nearest town, hospital and road out, before
  and after, in plain words: "near Bidur (1,341 buildings): likely cut off by road (likely
  impassable at Pasang Lhamu Highway, 2.87 km away, high confidence) … Needs field verification.";
- **rescue priorities**, **helicopter landing-zone candidates**, **later satellite passes**
  (receding water, possible lakes forming behind debris) and the **flood-path bonus** (trace the
  flood down the valley from any upstream point);
- a **one-page situation report** and an offline **field package** (GeoPackage, KML, GPS
  waypoints), all in a local dashboard.

On the August 2026 Bhote Koshi – Trishuli flood: 15.60 km² potentially affected, 3,332 buildings,
80.5 km of road and 163 bridges potentially affected, and 215 of 2,866 settlements likely cut off.

## How we built it
- **Radar change detection** on Copernicus Data Space (openEO): calibrated, terrain-corrected
  Sentinel-1, compared only on the same orbit track (up to two tracks), with speckle filtering
  and thresholds for new water, wet mud/debris and rough debris.
- **Terrain masks** from the Copernicus DEM (height above the river, slope, radar shadow) and a
  **confidence map** combining both tracks and Sentinel-2.
- **Pre-event OpenStreetMap** from the ohsome API at an explicit snapshot before the flood,
  rebuilt from full history and checked against ohsome's own counts.
- **Cut-off analysis** on our own road graph (scipy shortest paths), with a virtual "outside"
  node so villages near the edge are not wrongly cut off.
- **Our own AI model:** a U-Net trained from scratch on Sen1Floods11, exported to ONNX and run
  locally. No external AI service: nothing about an analysis leaves the laptop.
- Python, numpy, rasterio, geopandas, shapely, scipy, PyTorch (training only), onnxruntime,
  matplotlib, Streamlit. Data rules enforced in code and by tests; hash-pinned dependencies;
  secret scanning; CI on Windows and Ubuntu.

## Challenges we ran into
- **Narrow gorges:** radar shadow and layover hide the valley floor; we chose precision over
  false alarms and report the trade-off instead of tuning to the reference maps.
- **Debris looks two ways in radar** (rough boulders brighten, wet mud darkens), and a lost
  bridge sits over a channel that was water before and after, so bridges are judged by their
  approaches and the river reach next to them.
- **Strict data rules:** only the allowed inputs, OSM strictly before the event, and Copernicus
  EMS maps for checking only. We froze the method before opening the reference.
- **Live judging on unknown areas:** every stage reports ok / partial / skipped / failed and
  never crashes the run.

## Accomplishments that we're proud of
- Checked against the official Copernicus EMS maps (EMSR927): **precision 0.91–1.00** wherever we
  flag damage, extent **IoU 0.78** in the Bidur valley, and **39 of 52** of our key road breaks on
  roads EMS graded as damaged.
- Our model clearly beats a radar threshold rule on held-out test images (IoU 0.649 vs 0.445);
  on a whole flood event it never saw it is level or better (0.54–0.65 across trainings vs
  0.58), and we report honestly that it transfers poorly to gorges.
- Tested on 13 places the system had never seen, on four continents, plus 8 invalid inputs: no
  crash, and where data was missing (no radar pair over Porto Alegre 2024, no elevation data
  over the open sea) it said so in plain words instead of inventing a map. The tests also
  found and fixed three real problems: huge floods were slow (Storm Daniel: an hour became
  minutes, same results), one dropped connection could fail a step (now retried), and a
  dense city (454,718 buildings in Kathmandu) ran out of memory (now batched).
- Every number in the report, the situation report and the dashboard is read from the system's
  own results, never typed by hand.

## What we learned
More model capacity did not beat better data: a 4× larger network did not improve and was worse
on the unseen event. In mountains, terrain physics (height above the river, slope, radar
geometry) matters as much as the classifier. And for rescue use, the wording ("potentially
affected", "likely cut off … needs field verification") matters as much as the map.

## What's next
A Sentinel-2-only flood map for places without a radar before/after pair, training on Kuro
Siwo's before/after pairs, terrain inputs for the model, automatic alerts when a
new Sentinel-1 pass arrives, and a Nepali-language situation report.

## Responsibility
Educational prototype, not an operational tool; no images of victims. Data: Contains modified
Copernicus Sentinel data 2026. Produced using Copernicus WorldDEM-30 © DLR e.V. 2010–2014 and
© Airbus Defence and Space GmbH 2014–2018 provided under COPERNICUS by the European Union and
ESA; all rights reserved. © OpenStreetMap contributors. Training data: Bonafilia et al., CVPR
Workshops 2020 (Sen1Floods11). Validation (checking only): European Union, Copernicus Emergency
Management Service data.

---------- copy until here ----------

**Built with** (one tag at a time):
python, pytorch, onnxruntime, streamlit, numpy, scipy, geopandas, shapely, rasterio, matplotlib,
openeo, copernicus-data-space, sentinel-1, sentinel-2, copernicus-dem, openstreetmap, ohsome

**"Try it out" links:**
- https://github.com/itskiee/floodmap

**Image gallery** (in this order; 3:2 PNGs, each well under 5 MB, all our own maps with
attributions): `report/devpost/gallery_01.png` … `gallery_09.png`

**Video demo link:** the YouTube or Vimeo link of the 3-minute video. Set it to **Public** (or
Unlisted, if the hackathon rules allow it) so the judges can play it.

---

## Step 4: Additional info (judges and organizers only)

- **Team Name:** your choice, e.g. `Sampark` (solo entry: Nandika Agarwal)
- **Track Selection:** Track B, "Mapping Flood Damage from Space"
- **GitHub repository link:** `https://github.com/itskiee/floodmap`. It must be **public**
  before you submit (the field says "public GitHub repository URL").
- **Team leader email:** your email
- **Live demo link (optional):** leave empty. The system runs locally (README explains how);
  hosting it would need our Copernicus login on a server, which we do not do.
- If more fields appear below these, send a screenshot and I will draft them.
