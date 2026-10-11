"""Result objects for pipeline stages and whole runs.

Every stage returns a `StageResult` with an explicit status instead of raising, so a
failure in one stage (e.g. "no cloud-free Sentinel-2 image") is reported clearly while the
rest of the pipeline continues with what it has. `Results.to_json()` writes results.json,
which is also what the situation-report copilot (M5) reads: it is the only source of
numbers the copilot may use.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from floodmap import PIPELINE_VERSION, attribution

RESULTS_SCHEMA_VERSION = "0.1"


class StageStatus(StrEnum):
    OK = "ok"  # stage produced its full output
    PARTIAL = "partial"  # usable output with gaps (warnings say what is missing)
    SKIPPED = "skipped"  # not run: not implemented, not requested, or inputs missing
    FAILED = "failed"  # tried and failed; message says why, error_id points to the log


@dataclass
class StageResult:
    name: str
    status: StageStatus
    message: str = ""
    outputs: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    seconds: float = 0.0
    error_id: str | None = None

    @property
    def usable(self) -> bool:
        return self.status in (StageStatus.OK, StageStatus.PARTIAL)


@dataclass
class Results:
    run_id: str
    created_utc: str
    event_name: str
    event_date: str
    aoi: dict[str, Any]
    config_hash: str
    run_dir: Path
    stages: dict[str, StageResult] = field(default_factory=dict)
    pipeline_version: str = PIPELINE_VERSION
    schema_version: str = RESULTS_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        # Never write absolute local paths (they reveal user names / folder layout).
        data.pop("run_dir")
        data["stages"] = {name: _relative_paths(asdict(s), self.run_dir) for name, s in
                          self.stages.items()}
        data["attributions"] = list(attribution.DATA_ATTRIBUTIONS)
        if "osm" in self.stages:
            data["data_licenses"] = {"openstreetmap": attribution.OSM_DATA_LICENSE}
        data["disclaimer"] = attribution.DISCLAIMER
        return data

    def to_json(self) -> Path:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        path = self.run_dir / "results.json"
        path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        return path


def _relative_paths(obj: Any, base: Path) -> Any:
    """Recursively convert Path objects to paths relative to the run folder."""
    if isinstance(obj, Path):
        try:
            return obj.resolve().relative_to(base.resolve()).as_posix()
        except ValueError:
            return obj.name  # outside the run folder: keep only the file name
    if isinstance(obj, dict):
        return {k: _relative_paths(v, base) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_relative_paths(v, base) for v in obj]
    return obj
