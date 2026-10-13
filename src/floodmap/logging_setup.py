"""Logging with secret redaction.

Every log line, including exception tracebacks, passes through `redact()` before it is
written to the console or the log file. Two layers:
1. exact secret values registered at start-up (any key a future feature reads from .env);
2. patterns that look like credentials even if we did not register them
   (private-key blocks, Google OAuth tokens, "api_key=..." style assignments, ...).

Redaction happens in the Formatter (not a Filter) so that tracebacks, which the
Formatter renders, are covered as well.
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

REDACTED = "[REDACTED]"

_registered_secrets: set[str] = set()

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    # PEM private key blocks (service-account JSON contains one)
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    # Google OAuth access / refresh tokens and API keys
    re.compile(r"ya29\.[0-9A-Za-z_\-]{20,}"),
    re.compile(r"1//[0-9A-Za-z_\-]{30,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    # Common API key prefixes (defence in depth)
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    # key=value / key: value assignments for secret-looking names
    re.compile(
        r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password|private[_-]?key)"
        r"(\"?\s*[:=]\s*\"?)([^\s\"',}]+)"
    ),
)


def register_secret(value: str | None) -> None:
    """Add an exact secret value that must never appear in any log output."""
    if value and len(value) >= 6:  # very short values would redact ordinary words
        _registered_secrets.add(value)


def redact(text: str) -> str:
    """Replace registered secrets and credential-looking substrings with [REDACTED]."""
    for secret in sorted(_registered_secrets, key=len, reverse=True):
        text = text.replace(secret, REDACTED)
    for pattern in _SECRET_PATTERNS:
        if pattern.groups == 3:  # key=value pattern: keep the key name, hide the value
            text = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
        else:
            text = pattern.sub(REDACTED, text)
    return text


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


class ConsoleFormatter(RedactingFormatter):
    """One short line per message: tracebacks go only to the log file (a user or judge sees
    "Stage terrain failed [error 725b12d4] (details in logs/floodmap.log)", never a Python
    traceback; seen on the open-sea test 2026-10-14)."""

    def format(self, record: logging.LogRecord) -> str:
        short = logging.makeLogRecord(record.__dict__)
        short.exc_info, short.exc_text, short.stack_info = None, None, None
        text = super().format(short)
        return text + "  (details in logs/floodmap.log)" if record.exc_info else text


_CONFIGURED = False


def setup_logging(log_dir: Path | None = None, level: str = "INFO") -> logging.Logger:
    """Configure the 'floodmap' logger: concise console output + detailed log file.

    The log file (gitignored `logs/`) receives full tracebacks; the console shows short
    messages. Both are redacted. Safe to call more than once.
    """
    global _CONFIGURED
    logger = logging.getLogger("floodmap")
    if _CONFIGURED:
        return logger

    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(level.upper())
    console.setFormatter(ConsoleFormatter("%(levelname)s %(message)s"))
    logger.addHandler(console)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_dir / "floodmap.log", encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        logger.addHandler(file_handler)

    _CONFIGURED = True
    return logger
