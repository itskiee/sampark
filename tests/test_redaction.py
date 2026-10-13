"""Secrets must never appear in logs, tracebacks, results.json or printed settings."""

from __future__ import annotations

import io
import json
import logging
from datetime import date

from floodmap import cli
from floodmap.config import load_settings
from floodmap.logging_setup import REDACTED, RedactingFormatter, redact, register_secret
from floodmap.pipeline import register_stage, run_pipeline

FAKE_KEY = "sk-test-THIS-IS-A-FAKE-KEY-0123456789"


def _capture_logger(name: str) -> tuple[logging.Logger, io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(RedactingFormatter("%(message)s"))
    logger = logging.getLogger(name)
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    return logger, stream


def test_registered_secret_is_redacted_in_messages():
    register_secret("my-very-private-value-42")
    logger, stream = _capture_logger("test.redact.msg")
    logger.info("calling API with key %s", "my-very-private-value-42")
    out = stream.getvalue()
    assert "my-very-private-value-42" not in out
    assert REDACTED in out


def test_secret_is_redacted_inside_tracebacks():
    register_secret(FAKE_KEY)
    logger, stream = _capture_logger("test.redact.tb")
    try:
        raise RuntimeError(f"auth failed for key={FAKE_KEY}")
    except RuntimeError:
        logger.error("request failed", exc_info=True)
    out = stream.getvalue()
    assert "Traceback" in out
    assert FAKE_KEY not in out


def test_credential_patterns_are_redacted_even_if_not_registered():
    # Fake credentials are assembled from pieces at runtime so this source file contains no
    # credential-shaped strings: the repo's secret scanners stay strict, with no exceptions.
    pem = "PRIVATE" + " KEY-----"
    samples = [
        "api_key" + "=abcdef1234567890",
        'password: "hunter2hunter2"',
        "token " + "ya29." + "a0" + "x" * 30,
        "-----BEGIN " + pem + "\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\n-----END " + pem,
        "Authorization with " + "AIza" + "Sy" + "A" * 33,
    ]
    for text in samples:
        out = redact(text)
        assert REDACTED in out, f"not redacted: {text!r}"
    assert "abcdef1234567890" not in redact(samples[0])
    assert "hunter2hunter2" not in redact(samples[1])


def test_ordinary_text_is_untouched():
    text = "Flooded area 12.4 km2 near Syabrubesi; 3 bridges potentially affected."
    assert redact(text) == text


def test_settings_never_expose_environment_secrets(monkeypatch):
    """floodmap needs no secret; a key lying around in the environment is never shown."""
    monkeypatch.setenv("LLM_API_KEY", FAKE_KEY)
    settings = load_settings()
    assert FAKE_KEY not in repr(settings)
    assert FAKE_KEY not in str(settings.summary())


def test_config_show_masks_the_api_key(monkeypatch, capsys):
    monkeypatch.setenv("LLM_API_KEY", FAKE_KEY)
    assert cli.main(["config-show"]) == 0
    assert FAKE_KEY not in capsys.readouterr().out


def test_stage_error_with_secret_is_redacted_in_results(tmp_config, clean_registry):
    register_secret(FAKE_KEY)

    @register_stage("s1")
    def leaky_stage(ctx):
        raise ValueError(f"provider rejected key {FAKE_KEY}")

    results = run_pipeline([85.28, 28.10, 85.42, 28.30], date(2026, 8, 26), tmp_config,
                           stages=["s1"])
    assert FAKE_KEY not in results.stages["s1"].message
    text = (results.run_dir / "results.json").read_text(encoding="utf-8")
    assert FAKE_KEY not in text
    assert json.loads(text)["stages"]["s1"]["status"] == "failed"


def test_console_shows_no_traceback_but_the_log_file_keeps_it():
    """Users see one line + where the details are; the log file keeps the full traceback."""
    from floodmap.logging_setup import ConsoleFormatter

    console, full = io.StringIO(), io.StringIO()
    logger = logging.getLogger("test.console.short")
    h_console, h_file = logging.StreamHandler(console), logging.StreamHandler(full)
    h_console.setFormatter(ConsoleFormatter("%(levelname)s %(message)s"))
    h_file.setFormatter(RedactingFormatter("%(message)s"))
    logger.handlers, logger.propagate = [h_file, h_console], False  # file formats first
    try:
        raise ValueError(f"boom {FAKE_KEY}")
    except ValueError:
        logger.error("Stage terrain failed [error abc12345]", exc_info=True)
    assert "Traceback" not in console.getvalue()
    assert "details in logs/floodmap.log" in console.getvalue()
    assert "Traceback" in full.getvalue() and FAKE_KEY not in full.getvalue()
