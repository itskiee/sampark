"""Unseen-place test campaign (M9): run the full system on places it has never seen.

Each event file in configs/events/unseen/ is run exactly as a judge would run it
(`python -m floodmap run --event FILE`, a separate process). The script records the exit
code, wall time, every stage's status, time and message, and a few key numbers, all read
from the run's results.json. Nothing is typed by hand. A second set of runs feeds the CLI
invalid inputs (future date, sea, tiny / huge / inverted area, pre-Sentinel-1 date, bad date
text): the system must answer with a clear message, never a Python traceback.

This is a robustness and plausibility test, NOT an accuracy test: these places have no
reference map in this project (accuracy is measured once, on the case study).

Usage:
  .venv/Scripts/python scripts/run_unseen_places.py                 # all places, then invalid
  .venv/Scripts/python scripts/run_unseen_places.py --only ahr_2021 swat_2022
  .venv/Scripts/python scripts/run_unseen_places.py --invalid-only
Writes docs/testing/unseen_places.json and docs/testing/unseen_places.md.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess  # noqa: S404 - fixed commands of this project, no shell
import sys
import time
from pathlib import Path

from floodmap.config import REPO_ROOT

PLACES = REPO_ROOT / "configs" / "events" / "unseen"
OUT = REPO_ROOT / "docs" / "testing"
INVALID = {
    "future date": ["--bbox", "85.30", "27.70", "85.40", "27.80", "--date", "2026-12-01"],
    "date before Sentinel-1": ["--bbox", "85.30", "27.70", "85.40", "27.80", "--date",
                               "2012-06-01"],
    "open sea": ["--bbox", "89.50", "20.00", "89.70", "20.20", "--date", "2024-06-01"],
    "tiny area": ["--bbox", "85.300", "27.700", "85.301", "27.701", "--date", "2024-09-28"],
    "huge area": ["--bbox", "80", "25", "84", "29", "--date", "2024-09-28"],
    "inverted box": ["--bbox", "85.40", "27.80", "85.30", "27.70", "--date", "2024-09-28"],
    "bad date text": ["--bbox", "85.30", "27.70", "85.40", "27.80", "--date", "2024-13-40"],
    "missing date": ["--bbox", "85.30", "27.70", "85.40", "27.80"],
}


def _floodmap(args: list[str]) -> tuple[int, str, str, float]:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    t0 = time.time()
    proc = subprocess.run([sys.executable, "-m", "floodmap", "run", *args],  # noqa: S603
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=env, cwd=REPO_ROOT, check=False)
    return proc.returncode, proc.stdout, proc.stderr, round(time.time() - t0, 1)


def _key_numbers(r: dict) -> dict:
    st = r["stages"]

    def out(name):
        s = st.get(name) or {}
        return s.get("outputs") or {} if s.get("status") in ("ok", "partial") else {}

    conf, dmg, cut, seg = out("confidence"), out("damage"), out("cutoff"), out("segmentation")
    mot = (cut.get("by_network") or {}).get("motorable") or {}
    b = dmg.get("buildings") or {}
    return {
        "area_km2": r["aoi"]["area_km2"],
        "potentially_affected_km2": conf.get("affected_total_km2"),
        "high_confidence_km2": (conf.get("affected_by_confidence_km2") or {}).get("high"),
        "buildings_high_confidence": (b.get("by_confidence") or {}).get("high"),
        "buildings_in_area": b.get("of_total_in_area"),
        "buildings_potentially_affected": b.get("potentially_affected"),
        "settlements_assessed": cut.get("settlements_assessed"),
        "settlements_likely_cut_off": mot.get("likely cut off"),
        "model_new_water_km2": seg.get("new_water_km2"),
    }


def _stages(r: dict) -> dict:
    return {k: {"status": v["status"], "seconds": v.get("seconds"),
                "message": (v.get("message") or "")[:240],
                "warnings": [w[:160] for w in v.get("warnings") or []]}
            for k, v in r["stages"].items()}


def run_place(path: Path, later_passes: bool = False) -> dict:
    code, stdout, stderr, secs = _floodmap(["--event", str(path.relative_to(REPO_ROOT))]
                                           + (["--later-passes"] if later_passes else []))
    rec = {"place": path.stem, "exit_code": code, "wall_seconds": secs,
           "traceback": "Traceback (most recent call last)" in stderr}
    m = re.search(r"^Run (\S+) ", stdout, re.MULTILINE)
    if not m:
        rec["error"] = (stderr.strip().splitlines() or ["no output"])[-1][:300]
        return rec
    r = json.loads((REPO_ROOT / "outputs" / m.group(1) / "results.json").read_text("utf-8"))
    rec.update({"run_id": r["run_id"], "name": r["event_name"], "event_date": r["event_date"],
                "bbox": r["aoi"]["bbox"],
                "stages": _stages(r), "key": _key_numbers(r)})
    return rec


def run_invalid(label: str, args: list[str]) -> dict:
    code, stdout, stderr, secs = _floodmap(args)
    text = (stderr.strip() or stdout.strip()).splitlines()
    statuses = re.findall(r"^\s{2}(\w+)\s+(ok|partial|skipped|failed)\s", stdout, re.MULTILINE)
    answer = text[-1] if text else ""
    m = re.search(r"^Run (\S+) ", stdout, re.MULTILINE)
    if m:  # the run went ahead: the answer is the first stage that could not do its work
        r = json.loads((REPO_ROOT / "outputs" / m.group(1) / "results.json").read_text("utf-8"))
        bad = [v for v in r["stages"].values() if v["status"] == "failed"]
        answer = (f"run completed; {bad[0]['name']} failed: {bad[0]['message']}" if bad
                  else "run completed")
    return {"case": label, "args": " ".join(args), "exit_code": code, "wall_seconds": secs,
            "traceback": "Traceback (most recent call last)" in stderr + stdout,
            "answer": answer[:300], "stage_statuses": dict(statuses)}


def write_markdown(data: dict) -> None:
    lines = ["# Unseen-place test campaign", "",
             "*Generated by `scripts/run_unseen_places.py` from each run's results.json; "
             "nothing typed by hand.*", "",
             "Robustness and plausibility only: these places have no reference map in this "
             "project. Accuracy is measured once, on the case study (validation/results.md).",
             "Boxes are approximate, from general geography, not from any flood or damage map.",
             "", "## Places", "",
             "| Place | Date | Area km² | Exit | Time | Stages ok / partial / skipped / failed "
             "| Potentially affected km² (high confidence) "
             "| Buildings potentially affected (high) / in area "
             "| Settlements likely cut off / assessed |",
             "|---|---|---|---|---|---|---|---|---|"]
    for rec in data["places"].values():
        if "stages" not in rec:
            lines.append(f"| {rec['place']} | | | {rec['exit_code']} | {rec['wall_seconds']} s "
                         f"| no run: {rec.get('error', '')} | | | |")
            continue
        counts = {s: sum(v["status"] == s for v in rec["stages"].values())
                  for s in ("ok", "partial", "skipped", "failed")}
        k = rec["key"]

        def f(v, fmt="{:,}"):
            return "–" if v is None else fmt.format(v)

        lines.append(
            f"| {rec['name']} | {rec['event_date']} | {f(k['area_km2'], '{:,.0f}')} "
            f"| {rec['exit_code']} | {rec['wall_seconds'] / 60:.1f} min "
            f"| {counts['ok']} / {counts['partial']} / {counts['skipped']} / "
            f"**{counts['failed']}** | {f(k['potentially_affected_km2'], '{:.2f}')} "
            f"({f(k.get('high_confidence_km2'), '{:.2f}')}) "
            f"| {f(k['buildings_potentially_affected'])} "
            f"({f(k.get('buildings_high_confidence'))}) / {f(k['buildings_in_area'])} "
            f"| {f(k['settlements_likely_cut_off'])} / {f(k['settlements_assessed'])} |")
    notes = [f"- **{r['place']}**: {r['note']}" for r in data["places"].values() if r.get("note")]
    if notes:
        lines += ["", "## Notes", "", *notes]
    lines += ["", "## Stages that were not ok", "",
              "(The flood-path bonus is skipped everywhere: no upstream start point was given in "
              "these test files. It is not listed below.)", ""]
    for rec in data["places"].values():
        for name, s in (rec.get("stages") or {}).items():
            if name == "flood_path" and "No upstream start point" in s["message"]:
                continue
            if s["status"] != "ok":
                why = "; ".join(s.get("warnings") or [])
                lines.append(f"- **{rec['place']} / {name}** ({s['status']}): {s['message']}"
                             + (f" (warnings: {why})" if why else ""))
    if data.get("invalid"):
        lines += ["", "## Invalid inputs (must give a clear answer, never a traceback)", "",
                  "| Case | Exit | Traceback | Answer |", "|---|---|---|---|"]
        for rec in data["invalid"].values():
            answer = rec["answer"].replace("|", "/")
            lines.append(f"| {rec['case']} | {rec['exit_code']} | "
                         f"{'**YES**' if rec['traceback'] else 'no'} | {answer} |")
    (OUT / "unseen_places.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", help="place keys (file names without .yaml)")
    ap.add_argument("--invalid-only", action="store_true")
    ap.add_argument("--skip-invalid", action="store_true")
    ap.add_argument("--later-passes", action="store_true",
                    help="also track later passes (the first campaign ran with them on)")
    ap.add_argument("--refresh-only", action="store_true",
                    help="recompute key numbers from the saved runs and rewrite the summary")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    store = OUT / "unseen_places.json"
    data = json.loads(store.read_text("utf-8")) if store.is_file() else {}
    data.setdefault("places", {})
    data.setdefault("invalid", {})
    if args.refresh_only:
        for rec in data["places"].values():
            res = REPO_ROOT / "outputs" / str(rec.get("run_id")) / "results.json"
            if res.is_file():
                r = json.loads(res.read_text("utf-8"))
                rec["key"], rec["stages"] = _key_numbers(r), _stages(r)
        store.write_text(json.dumps(data, indent=1) + chr(10), encoding="utf-8")
        write_markdown(data)
        print(f"refreshed {OUT / 'unseen_places.md'}")
        return 0
    if not args.invalid_only:
        files = sorted(PLACES.glob("*.yaml"))
        if args.only:  # in the order given (e.g. the heaviest place last)
            known = {p.stem: p for p in files}
            missing = [k for k in args.only if k not in known]
            if missing:
                print(f"unknown place(s): {missing}; known: {sorted(known)}")
                return 2
            files = [known[k] for k in args.only]
        for path in files:
            print(f"--- {path.stem}", flush=True)
            data["places"][path.stem] = rec = run_place(path, args.later_passes)
            store.write_text(json.dumps(data, indent=1) + chr(10), encoding="utf-8")
            print(f"    exit {rec['exit_code']} in {rec['wall_seconds']} s; "
                  + (", ".join(f"{k}={v['status']}" for k, v in rec["stages"].items())
                     if "stages" in rec else rec.get("error", "")), flush=True)
    if not args.skip_invalid:
        for label, cli_args in INVALID.items():
            data["invalid"][label] = rec = run_invalid(label, cli_args)
            print(f"--- invalid: {label}: exit {rec['exit_code']}, traceback "
                  f"{rec['traceback']}: {rec['answer'][:120]}", flush=True)
            store.write_text(json.dumps(data, indent=1) + chr(10), encoding="utf-8")
    write_markdown(data)
    print(f"wrote {OUT / 'unseen_places.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
