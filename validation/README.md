# validation/ — checking results only

This folder holds the **Copernicus EMS EMSR927** reference products and the scripts that
compare our results with them (milestone M7).

Rules (breaking them = disqualification):
- Reference maps are used **only to check** results, never as pipeline input.
- Code in `src/`, `app/` and `scripts/` must never import from or read this folder.
  `tests/test_data_rules.py` enforces this on every test run, pre-commit and CI.
- Thresholds are **not** tuned against these maps.
- Raw downloads go in `validation/data/` (git-ignored); keep only scripts, checksums and
  computed metrics in git.

Credit: "European Union, Copernicus Emergency Management Service data".

## How the comparison is reproduced (M7)
1. Download the EMSR927 packages from
   https://mapping.emergency.copernicus.eu/activations/EMSR927/ into
   `validation/data/EMSR927/` (the owner did this on 2026-10-13; checksums of the exact files
   used: `EMSR927_SHA256SUMS.txt`).
2. `python -I validation/extract_emsr927.py`: unpacks data files only (unsafe paths refused,
   nothing executed; the OSM change file `.osc` is skipped and never read).
3. `python -I validation/compare_emsr927.py outputs/<case-study run> outputs/<AOI05 run>`:
   metrics in `results/metrics.json`, maps in `results/*.png`.
4. `python -I validation/make_results_md.py`: writes `results.md` from the metrics (no
   hand-typed numbers).

The method was frozen before step 2 (configs/default.yaml unchanged since commit 048c725,
SHA-256 prefix f6062fb0f6df0693). Nothing in `src/` was changed because of these results.
