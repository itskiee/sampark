# Security

floodmap is an educational prototype, but it is built with the controls we would expect
from a system used by a public authority. This file explains what we protect, from what,
and how. Every control listed here is implemented and, where possible, tested.

## What we protect

| Asset | Risk | Controls |
|---|---|---|
| Copernicus Data Space login | Account misuse, credit theft | Interactive login (`floodmap login`); the openEO client keeps the refresh token in the user profile, never in the repo. The pipeline only reuses it non-interactively and never prints it. |
| LLM API key (from M5) | Cost abuse | Only in `.env` (git-ignored) or environment variables; held as `SecretStr`; registered for log redaction; provider-side spend limit. |
| Personal information | Exposure in a public repo | Commits use a GitHub *noreply* email; `results.json` stores only relative paths (tested). |
| Integrity of results | Disqualifying data use, stale results | Data-rule allowlist enforced at runtime (`data_rules.py`) and statically (`tests/test_data_rules.py`); cache keys include config + code version; half-written cache entries are never reused. |
| Third-party material | Redistribution | Organizer PDFs and raw validation data are git-ignored. |

## Controls

**Secrets never enter the repository**
- `.gitignore` covers `.env*`, key files, credentials, caches, outputs, logs and validation
  data. A test (`test_sensitive_paths_are_git_ignored`) asks git itself to confirm this.
- `pre-commit` runs **gitleaks** (secret scanner) and **detect-private-key** before every
  commit, plus a large-file guard. Test fixtures build fake credentials at runtime, so
  the scanners run with **no allowlist exceptions**.
- CI re-scans the **full git history** with gitleaks (binary verified by SHA-256).

**Secrets never appear in output**
- All log output, including tracebacks, passes through a redacting formatter (registered
  secret values + credential-shaped patterns). Tested in `tests/test_redaction.py`.
- The UI/CLI shows short error messages with an error id; full tracebacks go only to the
  local, git-ignored `logs/floodmap.log`.
- `doctor` and `config-show` report only whether a secret is *set*.

**Network access is restricted**
- All our HTTP requests go through `floodmap/net.py`, which accepts only HTTPS to
  allowlisted hosts (ohsome, CDSE catalogue), always uses timeouts and bounded retries. A
  test fails if any other pipeline module imports an HTTP library.
- The openEO client is only given the CDSE endpoint constant; data collections are opened
  only through an allowlist of the permitted missions (`SENTINEL1_GRD`, `SENTINEL2_L2A`,
  `COPERNICUS_30`). Imports of other data platforms fail the data-rules test.

**Untrusted input is treated as data**
- Judge-provided areas (bbox / GeoJSON) are validated, size-capped and never used as
  file paths (`aoi.py`).
- YAML is read with `yaml.safe_load` only.
- From M5: OpenStreetMap tag values (user-generated) are sanitised and passed to the LLM as
  quoted data; the LLM has read-only lookups over our results, no actions, no network,
  no file access. Its output is validated against the results before display.

**Supply chain**
- Every dependency is pinned to an exact version **and SHA-256 hash**; installation uses
  `pip install --require-hashes`.
- `pip-audit` checks all pinned packages against known-vulnerability databases in CI.
- GitHub Actions are pinned to full commit SHAs; pre-commit hooks are pinned to commit SHAs.
- Dependabot reports outdated/vulnerable dependencies weekly.
- ruff runs bandit-derived security lint rules (`S`) on all code.

**CI has nothing to leak**
- Workflow token is read-only (`permissions: contents: read`), no repository secrets are
  configured or needed, checkout does not persist credentials, no `pull_request_target`,
  GitHub-hosted runners only. Tests that need a CDSE login run locally (`pytest -m cdse`).

## Operational checklist (maintainer)
- [ ] Repository private until submission; branch protection on `main` requiring CI.
- [ ] Secret scanning / push protection enabled where the GitHub plan allows.
- [ ] LLM provider spend limit set; CDSE account used only for this project.
- [ ] Demo video / screenshots: no `.env`, no terminal showing tokens, no signed-in tabs.
- [ ] Rotate / revoke all keys after the hackathon.

## Reporting a problem
Please open a GitHub security advisory (repository → Security → Report a vulnerability)
rather than a public issue.
