from __future__ import annotations

import json

from sampark import cli
from sampark.config import REPO_ROOT

TRISHULI = str(REPO_ROOT / "configs" / "events" / "trishuli_2026.yaml")


def test_config_show_prints_valid_json(capsys):
    assert cli.main(["config-show", "--event", TRISHULI]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["event"]["event_date"] == "2026-08-26"
    assert data["settings"]["secrets needed"].startswith("none")


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
    assert "secrets needed" in out
    assert code in (0, 1)


def test_console_output_never_crashes_on_legacy_code_page(monkeypatch):
    """A Windows pipe with cp1252 used to crash the run summary on "→" / "²"."""
    import io
    import sys

    from sampark.cli import _utf8_console

    raw = io.BytesIO()
    legacy = io.TextIOWrapper(raw, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", legacy)
    _utf8_console()
    print("Sentinel-2 2026-08-12 → 2026-08-27: 8.14 km² — नेपाल")
    sys.stdout.flush()
    assert "→".encode() in raw.getvalue()
