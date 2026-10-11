"""Single entry point: run_pipeline(aoi, event_date, config) -> Results.

Used identically by the CLI, the dashboard and the tests.

Robustness design:
- The AOI is validated first; a bad AOI raises AOIError (an input problem, shown to the user).
- Each stage runs inside its own try/except. A failure becomes a FAILED StageResult with a
  short message and an error id; the full traceback goes to the (redacted) log file only.
- A stage whose required inputs are missing is SKIPPED with the reason, not crashed.
- results.json is always written, even if stages fail, so the dashboard/copilot can show
  whatever was produced.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from floodmap.aoi import AOI, parse_aoi
from floodmap.cache import Cache, stable_hash
from floodmap.config import PipelineConfig, Settings, load_config, load_settings
from floodmap.logging_setup import redact, setup_logging
from floodmap.results import Results, StageResult, StageStatus

log = logging.getLogger("floodmap.pipeline")

ProgressFn = Callable[[str, str], None]  # (stage name, message)

# Execution order and the milestone that implements each stage.
STAGE_ORDER: tuple[tuple[str, str], ...] = (
    ("s1", "M1"),  # Sentinel-1 change detection
    ("terrain", "M2"),  # Copernicus DEM: slope, HAND, layover/shadow masks
    ("s2", "M2"),  # Sentinel-2 optical change when cloud-free
    ("confidence", "M2"),  # agreement of S1 / S2 / model
    ("flood_path", "M2"),  # bonus: downstream flow path from DEM
    ("osm", "M3"),  # pre-event OSM via ohsome
    ("damage", "M3"),  # affected buildings / roads / bridges
    ("cutoff", "M4"),  # reachability to town / hospital, before vs after
    ("priority", "M4"),  # rescue priority ranking
    ("copilot", "M5"),  # grounded EN/NE answers
    ("report", "M5"),  # one-page situation report
    ("landing_zones", "M8"),
    ("multidate", "M8"),
    ("segmentation", "M8"),
    ("exports", "M8"),  # GeoJSON / KML
)
STAGE_NAMES = tuple(name for name, _ in STAGE_ORDER)
_MILESTONE = dict(STAGE_ORDER)


@dataclass
class StageContext:
    """Everything a stage may use. Stages read upstream outputs from `results`."""

    aoi: AOI
    event_date: date
    config: PipelineConfig
    settings: Settings
    cache: Cache
    results: Results
    progress: ProgressFn
    focus_bbox: tuple[float, float, float, float] | None = None  # optional close-up window


@dataclass(frozen=True)
class StageDef:
    name: str
    fn: Callable[[StageContext], StageResult]
    requires: tuple[str, ...] = ()


REGISTRY: dict[str, StageDef] = {}


def register_stage(name: str, requires: tuple[str, ...] = ()):
    """Decorator used by stage modules to plug themselves into the pipeline."""
    if name not in STAGE_NAMES:
        raise ValueError(f"Unknown stage '{name}'")

    def decorator(fn: Callable[[StageContext], StageResult]):
        REGISTRY[name] = StageDef(name=name, fn=fn, requires=requires)
        return fn

    return decorator


def _expand_with_requirements(requested: list[str]) -> list[str]:
    """Add the stages that requested stages depend on, keep pipeline order."""
    needed: set[str] = set()

    def add(name: str) -> None:
        if name in needed:
            return
        needed.add(name)
        stage = REGISTRY.get(name)
        for req in stage.requires if stage else ():
            add(req)

    for name in requested:
        add(name)
    return [n for n in STAGE_NAMES if n in needed]


def _noop_progress(stage: str, message: str) -> None:
    log.info("[%s] %s", stage, message)


def run_pipeline(
    aoi: Any,
    event_date: date,
    config: PipelineConfig | None = None,
    *,
    stages: list[str] | None = None,
    event_name: str = "custom area",
    settings: Settings | None = None,
    progress: ProgressFn | None = None,
    focus_bbox: tuple[float, float, float, float] | None = None,
) -> Results:
    config = config or load_config()
    settings = settings or load_settings()
    progress = progress or _noop_progress
    setup_logging(config.resolve(config.logging.dir), config.logging.level)

    unknown = [s for s in stages or [] if s not in STAGE_NAMES]
    if unknown:
        raise ValueError(f"Unknown stage(s): {unknown}. Valid: {list(STAGE_NAMES)}")

    parsed = parse_aoi(aoi, config.limits.max_aoi_km2, config.limits.min_aoi_km2)
    now = datetime.now(UTC)
    run_id = f"{now:%Y%m%dT%H%M%SZ}_{parsed.aoi_hash[:8]}"
    results = Results(
        run_id=run_id,
        created_utc=now.isoformat(timespec="seconds"),
        event_name=event_name,
        event_date=event_date.isoformat(),
        aoi=parsed.summary(),
        config_hash=stable_hash(config.model_dump(mode="json")),
        run_dir=config.resolve(config.output.dir) / run_id,
    )
    ctx = StageContext(
        aoi=parsed,
        event_date=event_date,
        config=config,
        settings=settings,
        cache=Cache(config.resolve(config.cache.dir), config.cache.enabled),
        results=results,
        progress=progress,
        focus_bbox=focus_bbox,
    )

    to_run = _expand_with_requirements(stages) if stages else list(STAGE_NAMES)
    log.info("Run %s: %s, event %s, AOI %.1f km²", run_id, event_name, event_date, parsed.area_km2)
    try:
        for name in to_run:
            results.stages[name] = _run_stage(name, ctx)
    finally:
        path = results.to_json()
        log.info("Results written to %s", path.relative_to(config.resolve(config.output.dir)))
    return results


def _run_stage(name: str, ctx: StageContext) -> StageResult:
    stage = REGISTRY.get(name)
    if stage is None:
        return StageResult(
            name, StageStatus.SKIPPED, f"Not implemented yet (milestone {_MILESTONE[name]})."
        )

    missing = [
        req for req in stage.requires
        if req not in ctx.results.stages or not ctx.results.stages[req].usable
    ]
    if missing:
        return StageResult(name, StageStatus.SKIPPED, f"Needs stage(s) {missing}, not available.")

    ctx.progress(name, "started")
    start = time.perf_counter()
    try:
        result = stage.fn(ctx)
    except Exception as exc:  # deliberate catch-all: one stage must never crash the run
        error_id = uuid.uuid4().hex[:8]
        log.error("Stage %s failed [error %s]", name, error_id, exc_info=True)
        result = StageResult(
            name,
            StageStatus.FAILED,
            f"{type(exc).__name__}: {redact(str(exc))[:300]}",
            error_id=error_id,
        )
    result.seconds = round(time.perf_counter() - start, 2)
    ctx.progress(name, f"{result.status.value} ({result.seconds}s) {result.message}".strip())
    return result


# Register the implemented stages (each module uses @register_stage). Imported last so the
# stage modules can import register_stage/StageContext from this, fully defined, module.
import floodmap.stages  # noqa: E402,F401
