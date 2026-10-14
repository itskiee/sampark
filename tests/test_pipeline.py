from __future__ import annotations

import json
from datetime import date

import pytest

from sampark import attribution
from sampark.aoi import AOIError
from sampark.config import REPO_ROOT
from sampark.pipeline import STAGE_NAMES, register_stage, run_pipeline
from sampark.results import StageResult, StageStatus

FOCUS = [85.28, 28.10, 85.42, 28.30]
DAY = date(2026, 8, 26)


def test_skeleton_run_skips_every_stage_and_writes_results(tmp_config, clean_registry):
    results = run_pipeline(FOCUS, DAY, tmp_config, event_name="test")
    assert list(results.stages) == list(STAGE_NAMES)
    assert all(s.status == StageStatus.SKIPPED for s in results.stages.values())

    text = (results.run_dir / "results.json").read_text(encoding="utf-8")
    data = json.loads(text)
    assert data["event_date"] == "2026-08-26"
    assert data["attributions"] == list(attribution.DATA_ATTRIBUTIONS)
    assert "not an operational tool" in data["disclaimer"]
    # no absolute local paths (privacy: user names, folder layout)
    assert str(REPO_ROOT) not in text and "Users" not in text


def test_failing_stage_does_not_stop_the_run(tmp_config, clean_registry):
    @register_stage("s1")
    def broken(ctx):
        raise RuntimeError("simulated data-source outage")

    @register_stage("osm")
    def fine(ctx):
        return StageResult("osm", StageStatus.OK, "42 buildings")

    results = run_pipeline(FOCUS, DAY, tmp_config, stages=["s1", "osm"])
    assert results.stages["s1"].status == StageStatus.FAILED
    assert results.stages["s1"].error_id
    assert "simulated data-source outage" in results.stages["s1"].message
    assert results.stages["osm"].status == StageStatus.OK


def test_stage_with_missing_input_is_skipped_not_crashed(tmp_config, clean_registry):
    @register_stage("s1")
    def no_pair(ctx):
        return StageResult("s1", StageStatus.FAILED, "no same-track Sentinel-1 pair")

    @register_stage("terrain", requires=("s1",))
    def terrain(ctx):  # pragma: no cover - must not be called
        raise AssertionError("should have been skipped")

    results = run_pipeline(FOCUS, DAY, tmp_config, stages=["terrain"])
    assert list(results.stages) == ["s1", "terrain"]  # dependency added automatically
    assert results.stages["terrain"].status == StageStatus.SKIPPED
    assert "s1" in results.stages["terrain"].message


def test_optional_input_runs_along_and_its_failure_does_not_skip(tmp_config, clean_registry):
    @register_stage("s1")
    def s1(ctx):
        return StageResult("s1", StageStatus.OK)

    @register_stage("s2")
    def cloudy(ctx):
        return StageResult("s2", StageStatus.SKIPPED, "too cloudy")

    @register_stage("confidence", requires=("s1",), uses=("s2",))
    def confidence(ctx):
        return StageResult("confidence", StageStatus.OK, f"s2 ran: {'s2' in ctx.results.stages}")

    results = run_pipeline(FOCUS, DAY, tmp_config, stages=["confidence"])
    assert list(results.stages) == ["s1", "s2", "confidence"]  # optional input pulled in
    assert results.stages["confidence"].status == StageStatus.OK
    assert results.stages["confidence"].message == "s2 ran: True"


def test_invalid_aoi_raises_input_error(tmp_config, clean_registry):
    with pytest.raises(AOIError):
        run_pipeline([85.45, 27.85, 85.10, 28.32], DAY, tmp_config)


def test_unknown_stage_name_is_rejected(tmp_config, clean_registry):
    with pytest.raises(ValueError, match="Unknown stage"):
        run_pipeline(FOCUS, DAY, tmp_config, stages=["teleport"])


def test_register_unknown_stage_rejected():
    with pytest.raises(ValueError):
        register_stage("not-a-stage")
