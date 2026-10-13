from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from floodmap.config import (
    REPO_ROOT,
    ConfigError,
    EventSpec,
    deep_merge,
    load_config,
    load_event,
)

TRISHULI = REPO_ROOT / "configs" / "events" / "trishuli_2026.yaml"


def test_default_config_loads():
    cfg = load_config()
    assert cfg.limits.max_aoi_km2 > cfg.limits.min_aoi_km2 > 0
    assert cfg.resolve(cfg.cache.dir) == REPO_ROOT / "cache"


def test_trishuli_event_loads():
    event = load_event(TRISHULI)
    assert event.event_date == date(2026, 8, 26)
    assert event.aoi.bbox == (85.10, 27.85, 85.45, 28.32)
    assert event.focus_bbox is not None


def test_event_overrides_are_applied():
    event = load_event(TRISHULI).model_copy(
        update={"config_overrides": {"limits": {"max_aoi_km2": 999}}}
    )
    cfg = load_config(event)
    assert cfg.limits.max_aoi_km2 == 999
    assert cfg.limits.min_aoi_km2 == load_config().limits.min_aoi_km2  # untouched sibling


def test_typo_in_config_key_is_an_error():
    with pytest.raises(ConfigError, match="max_aoi_km"):
        load_config(overrides={"limits": {"max_aoi_kmm2": 10}})


def test_invalid_values_are_errors():
    with pytest.raises(ConfigError):
        load_config(overrides={"logging": {"level": "LOUD"}})
    with pytest.raises(ConfigError):
        load_config(overrides={"limits": {"max_aoi_km2": -1}})


def test_extra_config_file_layer(tmp_path):
    extra = tmp_path / "extra.yaml"
    extra.write_text("network:\n  retries: 0\n", encoding="utf-8")
    assert load_config(extra_files=(extra,)).network.retries == 0


def test_missing_and_malformed_files(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_event(tmp_path / "nope.yaml")
    bad = tmp_path / "list.yaml"
    bad.write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="mapping"):
        load_event(bad)


def test_aoi_needs_exactly_one_source():
    base = {"name": "x", "event_date": "2026-08-26"}
    with pytest.raises(ValueError):
        EventSpec.model_validate({**base, "aoi": {}})
    with pytest.raises(ValueError):
        EventSpec.model_validate({**base, "aoi": {"bbox": [1, 2, 3, 4], "geojson": "a.geojson"}})


def test_deep_merge_semantics():
    base = {"a": {"b": 1, "c": 2}, "d": [1, 2]}
    merged = deep_merge(base, {"a": {"c": 3}, "d": [9]})
    assert merged == {"a": {"b": 1, "c": 3}, "d": [9]}
    assert base == {"a": {"b": 1, "c": 2}, "d": [1, 2]}  # input not mutated


def test_absolute_paths_are_kept():
    cfg = load_config()
    absolute = Path(REPO_ROOT.anchor) / "somewhere"
    assert cfg.resolve(absolute) == absolute


def test_later_passes_off_by_default_on_for_the_case_study():
    """Owner decision 2026-10-13: a new analysis answers where/what/who first; the case study
    (weeks after the event) keeps tracking later passes."""
    from floodmap.config import REPO_ROOT, load_config, load_event

    assert load_config().multidate.enabled is False
    event = load_event(REPO_ROOT / "configs" / "events" / "trishuli_2026.yaml")
    assert load_config(event).multidate.enabled is True
    assert load_config(overrides={"multidate": {"enabled": True}}).multidate.enabled is True
