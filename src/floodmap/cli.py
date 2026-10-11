"""Command-line interface.

    python -m floodmap run --event configs/events/trishuli_2026.yaml [--stages s1,terrain]
    python -m floodmap run --bbox 85.28 28.10 85.42 28.30 --date 2026-08-26 --name "my area"
    python -m floodmap login                   # one-time Copernicus Data Space login
    python -m floodmap doctor [--offline]      # pre-flight check before a live demo
    python -m floodmap config-show [--event FILE]

Exit codes: 0 = finished (individual stages may still report failed/skipped),
            1 = doctor found a blocking problem or an unexpected error,
            2 = invalid input (AOI, date, config).
"""

from __future__ import annotations

import argparse
import importlib
import json
import shutil
import sys
from datetime import date
from pathlib import Path

from floodmap import __version__


def _parse_date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"date must be YYYY-MM-DD, got '{text}'") from exc


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="floodmap", description=__doc__.split("\n\n")[0])
    parser.add_argument("--version", action="version", version=f"floodmap {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the pipeline for an event file or a bbox + date")
    src = run.add_mutually_exclusive_group(required=True)
    src.add_argument("--event", type=Path, help="event YAML (configs/events/...)")
    src.add_argument(
        "--bbox", type=float, nargs=4, metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT")
    )
    run.add_argument("--date", type=_parse_date, help="flood date YYYY-MM-DD (with --bbox)")
    run.add_argument("--name", default="custom area", help="label for a --bbox run")
    run.add_argument("--stages", help="comma-separated stages (default: all)")
    run.add_argument("--flood-path-start", type=float, nargs=2, metavar=("LON", "LAT"),
                     help="upstream point for the flood-path bonus")
    run.add_argument("--config", type=Path, action="append", default=[], help="extra config YAML")

    sub.add_parser("login", help="log in to Copernicus Data Space (once; token in your profile)")

    doctor = sub.add_parser("doctor", help="check environment, credentials and services")
    doctor.add_argument("--offline", action="store_true", help="skip network checks")

    show = sub.add_parser("config-show", help="print the effective configuration")
    show.add_argument("--event", type=Path)
    return parser


def _cmd_run(args: argparse.Namespace) -> int:
    from floodmap.aoi import AOIError
    from floodmap.config import ConfigError, load_config, load_event
    from floodmap.pipeline import run_pipeline

    try:
        if args.event:
            event = load_event(args.event)
            aoi_spec = list(event.aoi.bbox) if event.aoi.bbox else event.aoi.geojson
            event_date, name, focus = event.event_date, event.name, event.focus_bbox
            start = event.flood_path_start
        else:
            if args.date is None:
                print("error: --date is required with --bbox", file=sys.stderr)
                return 2
            event, aoi_spec, event_date, name = None, list(args.bbox), args.date, args.name
            focus, start = None, None
        if args.flood_path_start:
            start = tuple(args.flood_path_start)
        config = load_config(event, tuple(args.config))
        stages = [s.strip() for s in args.stages.split(",")] if args.stages else None
        results = run_pipeline(aoi_spec, event_date, config, stages=stages, event_name=name,
                               focus_bbox=focus, flood_path_start=start)
    except (AOIError, ConfigError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    area = results.aoi["area_km2"]
    print(f"\nRun {results.run_id} — {name} — event {event_date} — AOI {area} km²")
    for stage in results.stages.values():
        extra = f" [error id {stage.error_id}, see logs/floodmap.log]" if stage.error_id else ""
        print(f"  {stage.name:<14} {stage.status.value:<8} {stage.seconds:>6.1f}s  "
              f"{stage.message}{extra}")
    print(f"Results: {results.run_dir / 'results.json'}")
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    from floodmap.config import load_config, load_settings

    rows: list[tuple[str, str, str]] = []

    def check(name: str, status: str, detail: str = "") -> None:
        rows.append((name, status, detail))

    ok_py = sys.version_info >= (3, 11)
    check("python", "OK" if ok_py else "FAIL", sys.version.split()[0])

    missing = []
    for mod in ("openeo", "numpy", "scipy", "skimage", "rasterio", "shapely", "pyproj",
                "geopandas", "pyogrio", "matplotlib", "yaml", "pydantic", "dotenv", "requests"):
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(mod)
    check("packages", "FAIL" if missing else "OK",
          f"missing: {missing}" if missing else "all importable")

    try:
        config = load_config()
        check("config", "OK", "configs/default.yaml valid")
    except Exception as exc:
        check("config", "FAIL", str(exc)[:200])
        config = None

    settings = load_settings()
    from floodmap.config import REPO_ROOT

    has_env = (REPO_ROOT / ".env").is_file()
    check(".env", "OK" if has_env else "INFO",
          "found" if has_env else "not present (only needed for the LLM key from M5)")
    for key, value in settings.summary().items():
        check(f"  {key}", "INFO", value)

    if config is not None:
        for label, folder in (("cache dir", config.cache.dir), ("output dir", config.output.dir),
                              ("log dir", config.logging.dir)):
            path = config.resolve(folder)
            try:
                path.mkdir(parents=True, exist_ok=True)
                probe = path / ".write_test"
                probe.write_text("ok", encoding="utf-8")
                probe.unlink()
                check(label, "OK", "writable")
            except OSError as exc:
                check(label, "FAIL", str(exc)[:200])
        free_gb = shutil.disk_usage(config.resolve(config.cache.dir)).free / 1e9
        check("disk space", "OK" if free_gb >= 5 else "WARN", f"{free_gb:,.0f} GB free")

    if args.offline:
        for name in ("cdse catalogue", "cdse login", "ohsome api"):
            check(name, "SKIP", "--offline")
    else:
        from datetime import UTC, datetime, timedelta

        from floodmap.catalogue import search_s1_scenes

        try:
            now = datetime.now(UTC)
            scenes = search_s1_scenes((85.28, 28.10, 85.42, 28.30), now - timedelta(days=30), now,
                                      timeout_s=60, retries=1)
            check("cdse catalogue", "OK", f"reachable; {len(scenes)} Sentinel-1 scenes over "
                                          "Syabrubesi in the last 30 days")
        except Exception as exc:
            check("cdse catalogue", "FAIL", str(exc)[:200])

        from floodmap.cdse_client import CDSEError, connect

        try:
            connect()
            check("cdse login", "OK", "logged in (token stored in your user profile)")
        except CDSEError as exc:
            check("cdse login", "FAIL", str(exc))

        from floodmap.data_rules import ohsome_snapshot
        from floodmap.ohsome_client import latest_data_timestamp

        try:
            latest = latest_data_timestamp()
            snap = ohsome_snapshot(date(2026, 8, 26), latest)
            check("ohsome api", "OK", f"data until {latest:%Y-%m-%d %H:%M}Z; "
                                      f"Trishuli snapshot would be {snap:%Y-%m-%d}")
        except Exception as exc:
            check("ohsome api", "FAIL", str(exc)[:200])

    check("llm key", "OK" if settings.llm_api_key else "WARN",
          "set (hidden)" if settings.llm_api_key else "not set (needed from milestone M5)")

    print("floodmap doctor — secrets are never displayed\n")
    for name, status, detail in rows:
        print(f"  {status:<5} {name:<26} {detail}")
    blocking = [r for r in rows if r[1] == "FAIL"]
    verdict = f"{len(blocking)} blocking problem(s)." if blocking else "All required checks passed."
    print(f"\n{verdict}")
    return 1 if blocking else 0


def _cmd_config_show(args: argparse.Namespace) -> int:
    from floodmap.config import ConfigError, load_config, load_event, load_settings

    try:
        event = load_event(args.event) if args.event else None
        config = load_config(event)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    out = {"config": config.model_dump(mode="json"), "settings": load_settings().summary()}
    if event:
        out["event"] = event.model_dump(mode="json")
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


def _cmd_login(args: argparse.Namespace) -> int:
    from floodmap.cdse_client import CDSEError, login

    print("Logging in to Copernicus Data Space. If a link and code appear, open the link in "
          "your browser, sign in and enter the code.")
    try:
        login()
    except CDSEError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print("Logged in. The token is stored in your user profile (not in this repository).")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    handlers = {"run": _cmd_run, "doctor": _cmd_doctor, "config-show": _cmd_config_show,
                "login": _cmd_login}
    return handlers[args.command](args)
