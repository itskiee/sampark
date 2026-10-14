"""Dashboard: privacy settings and a headless render (Streamlit's own test harness)."""

from __future__ import annotations

import json
import tomllib

import pytest

from sampark.config import REPO_ROOT, load_config

APP = str(REPO_ROOT / "app" / "streamlit_app.py")


def test_streamlit_privacy_and_safety_settings():
    cfg = tomllib.loads((REPO_ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    assert cfg["browser"]["gatherUsageStats"] is False  # no data leaves the machine
    assert cfg["server"]["address"] == "localhost"  # not reachable from the network
    assert cfg["client"]["showErrorDetails"] == "none"  # no tracebacks/paths in the browser


@pytest.fixture
def isolated_outputs(tmp_path, monkeypatch):
    cfg = load_config(overrides={"output": {"dir": str(tmp_path / "outputs")},
                                 "cache": {"dir": str(tmp_path / "cache")},
                                 "logging": {"dir": str(tmp_path / "logs")}})
    monkeypatch.setattr("sampark.config.load_config", lambda *a, **k: cfg)
    return tmp_path / "outputs"


def _app():
    from streamlit.testing.v1 import AppTest

    return AppTest.from_file(APP, default_timeout=60)


def test_dashboard_without_runs(isolated_outputs):
    at = _app().run()
    assert not at.exception
    assert any("No analysis yet" in i.value for i in at.info)
    assert any("Educational prototype" in c.value for c in at.caption)


def test_dashboard_renders_partial_run(isolated_outputs):
    """A run where some stages failed must render with 'Not available' messages, no crash."""
    run = isolated_outputs / "20261013T000000Z_test"
    run.mkdir(parents=True)
    results = {
        "run_id": run.name, "event_name": "test valley", "event_date": "2026-08-26",
        "aoi": {"bbox": [85.3, 28.1, 85.4, 28.2], "area_km2": 108.0},
        "stages": {
            "s1": {"status": "ok", "message": "85 ascending", "outputs": {}},
            "confidence": {"status": "ok", "message": "ok", "outputs": {
                "affected_total_km2": 1.25, "sources": ["Sentinel-1 85 asc"],
                "affected_by_confidence_km2": {"high": 1.0, "medium": 0.2, "low": 0.05},
                "files": {"png": []}}},
            "osm": {"status": "failed", "message": "NetworkError: ohsome unreachable",
                    "outputs": {}},
            "damage": {"status": "skipped", "message": "Needs stage(s) ['osm']", "outputs": {}},
        },
        "attributions": [], "disclaimer": "",
    }
    (run / "results.json").write_text(json.dumps(results), encoding="utf-8")
    at = _app().run()
    assert not at.exception, at.exception
    assert at.title[0].value == "test valley"
    assert any("1.25 km²" in m.value for m in at.metric)
    at.radio(key="section").set_value("What was damaged?").run()  # one section at a time
    assert not at.exception, at.exception
    assert any("Needs stage(s) ['osm']" in i.value for i in at.info)


def test_later_passes_tab_offers_the_follow_up_when_not_requested(isolated_outputs):
    """Later passes are off for a new analysis: the tab explains why and offers one button."""
    from sampark.stages.multidate import NOT_REQUESTED

    run = isolated_outputs / "20261014T000000Z_test"
    run.mkdir(parents=True)
    results = {
        "run_id": run.name, "event_name": "test valley", "event_date": "2026-08-26",
        "aoi": {"bbox": [85.3, 28.1, 85.4, 28.2], "area_km2": 108.0},
        "stages": {"multidate": {"status": "skipped", "message": NOT_REQUESTED, "outputs": {}}},
        "attributions": [], "disclaimer": "",
    }
    (run / "results.json").write_text(json.dumps(results), encoding="utf-8")
    at = _app().run()
    assert not at.exception, at.exception
    at.radio(key="section").set_value("Later passes").run()
    assert not at.exception, at.exception
    assert any("were not tracked" in i.value for i in at.info)
    assert any(b.label == "Track later passes for this area" for b in at.button)
    assert any("Sampark" in t.value for t in at.sidebar.title)
