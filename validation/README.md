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
