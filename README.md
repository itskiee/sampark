# floodmap — Mapping flood damage from space

> **Educational prototype** built for the Multimodal AI Hackathon 2026 (Track B). Not an
> operational tool. All results are estimates from satellite data and pre-event
> OpenStreetMap and must be verified on the ground.

Given an area and a flood date, the system answers three questions for rescue teams:

1. **Where did the flood hit?** Flooded and debris-covered areas from Sentinel-1 radar
   (and Sentinel-2 optical when cloud-free), before vs after.
2. **What was damaged?** Pre-event OpenStreetMap buildings, roads and bridges overlaid on
   the flood map.
3. **Who is cut off?** Settlements that lost their road connection to the nearest town
   or hospital.

**Status:** milestone M0 (project setup). See [PLAN.md](PLAN.md).

## Quick start

Requires Python 3.11–3.13 (developed on 3.12) and a free
[Copernicus Data Space Ecosystem](https://dataspace.copernicus.eu) account, the Sentinel
data source named in the hackathon brief.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install --require-hashes -r requirements.txt   # Windows
# .venv/bin/python -m pip install --require-hashes -r requirements.txt     # Linux / macOS
.venv/Scripts/python -m pip install --no-deps -e .
```

`--require-hashes` makes pip verify every downloaded package against the SHA-256 recorded
in `requirements.txt`; installation stops if anything does not match.

### Copernicus Data Space access
1. Create a free account at https://dataspace.copernicus.eu.
2. `.venv/Scripts/python -m floodmap login`: open the link it prints, sign in, enter the
   code. The login token is stored in your user profile, not in this repository.

Scene search uses the public CDSE STAC catalogue; processing (Sentinel-1 backscatter with
Copernicus DEM terrain correction, Sentinel-2 L2A, Copernicus DEM) uses CDSE openEO.

### Check and run
```bash
.venv/Scripts/python -m floodmap doctor
.venv/Scripts/python scripts/check_cdse.py
.venv/Scripts/python -m floodmap run --event configs/events/trishuli_2026.yaml
```

### Tests
```bash
.venv/Scripts/python -m pip install --require-hashes -r requirements-dev.txt
.venv/Scripts/python -m pytest              # offline tests (default)
.venv/Scripts/python -m pytest -m network   # live public catalogue (no login)
.venv/Scripts/python -m pytest -m cdse      # live CDSE processing (needs login)
```

## Data rules
Only Sentinel-1, Sentinel-2, Copernicus DEM (GLO-30), OpenStreetMap as it was **before**
the event (via the ohsome API) and the listed training datasets are used as inputs, from
the sources named in the brief: Copernicus Data Space Ecosystem and the ohsome API.
Copernicus EMS (EMSR927) reference maps are used **only** in `validation/` to check
results; pipeline code never reads them. This is enforced by `tests/test_data_rules.py`.

## Security
See [SECURITY.md](SECURITY.md). Secrets live only in `.env` (git-ignored) or your user
profile; CI runs without any secrets.

## Updating dependencies
```bash
.venv/Scripts/uv pip compile pyproject.toml --universal --python-version 3.11 --generate-hashes --no-header -o requirements.txt
.venv/Scripts/uv pip compile pyproject.toml --extra dev --universal --python-version 3.11 --generate-hashes --no-header -o requirements-dev.txt
```

## Attributions
- Contains modified Copernicus Sentinel data 2026.
- Produced using Copernicus WorldDEM-30 © DLR e.V. 2010–2014 and © Airbus Defence and
  Space GmbH 2014–2018 provided under COPERNICUS by the European Union and ESA; all
  rights reserved.
- © OpenStreetMap contributors (ODbL).
- Validation: European Union, Copernicus Emergency Management Service data.

Training data (cited when used): Bountos et al., 2024 (Kuro Siwo); Bonafilia et al., CVPR
Workshops 2020 (Sen1Floods11).

## License
Code: MIT (see [LICENSE](LICENSE)). OSM-derived data: ODbL.
