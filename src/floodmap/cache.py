"""File cache for intermediate results.

Layout:  cache/<aoi_hash>_<event_date>/<stage>_<settings_hash>/
The settings hash covers the stage's own config AND the pipeline version, so changing a
threshold or the processing code never serves a stale result from an earlier run.

A stage folder only counts as cached once it contains the `_COMPLETE` marker, written
last. If a run crashes halfway through a download, the half-written folder is ignored and
rebuilt next time instead of being used.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import date
from pathlib import Path
from typing import Any

from floodmap import PIPELINE_VERSION

_MARKER = "_COMPLETE"


def stable_hash(obj: Any, length: int = 12) -> str:
    """Deterministic hash of JSON-serialisable data (key order does not matter)."""
    payload = json.dumps(obj, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]


class Cache:
    def __init__(self, root: Path, enabled: bool = True) -> None:
        self.root = root
        self.enabled = enabled

    def stage_dir(
        self, aoi_hash: str, event_date: date, stage: str, stage_settings: dict[str, Any]
    ) -> Path:
        key = stable_hash({"settings": stage_settings, "pipeline_version": PIPELINE_VERSION})
        return self.root / f"{aoi_hash}_{event_date.isoformat()}" / f"{stage}_{key}"

    def is_complete(self, folder: Path) -> bool:
        return self.enabled and (folder / _MARKER).is_file()

    def prepare(self, folder: Path) -> Path:
        """Start a fresh (incomplete) cache folder, discarding any half-written leftovers."""
        if folder.exists() and not (folder / _MARKER).is_file():
            shutil.rmtree(folder)
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def mark_complete(self, folder: Path, metadata: dict[str, Any] | None = None) -> None:
        (folder / _MARKER).write_text(
            json.dumps(metadata or {}, indent=2, default=str), encoding="utf-8"
        )
