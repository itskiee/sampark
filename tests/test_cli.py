from __future__ import annotations

import json

from floodmap import cli
from floodmap.config import REPO_ROOT

TRISHULI = str(REPO_ROOT / "configs" / "events" / "trishuli_2026.yaml")


def test_config_show_prints_valid_json(capsys):
    assert cli.main(["config-show", "--event", TRISHULI]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["event"]["event_date"] == "2026-08-26"
    assert "LLM_API_KEY" in data["settings"]


def test_run_with_inverted_bbox_returns_input_error(capsys):
    code = cli.main(["run", "--bbox", "85.45", "27.85", "85.10", "28.32", "--date", "2026-08-26"])
    assert code == 2
    assert "min < max" in capsys.readouterr().err


def test_run_bbox_requires_date(capsys):
    assert cli.main(["run", "--bbox", "85.28", "28.10", "85.42", "28.30"]) == 2


def test_missing_event_file_is_input_error(capsys):
    assert cli.main(["run", "--event", "configs/events/does_not_exist.yaml"]) == 2
    assert "not found" in capsys.readouterr().err


def test_doctor_offline_runs_and_hides_secrets(monkeypatch, capsys):
    monkeypatch.setenv("LLM_API_KEY", "sk-test-FAKE-doctor-key-0123456789")
    code = cli.main(["doctor", "--offline"])
    out = capsys.readouterr().out
    assert "sk-test-FAKE-doctor-key" not in out
    assert "set (hidden)" in out
    assert code in (0, 1)
