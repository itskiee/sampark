from __future__ import annotations

import pytest

from sampark import pipeline
from sampark.config import load_config


@pytest.fixture(scope="session", autouse=True)
def _isolated_logging(tmp_path_factory):
    """Configure logging once, into a temp folder, so tests never write to the repo's logs/."""
    from sampark.logging_setup import setup_logging

    setup_logging(tmp_path_factory.mktemp("logs"), "WARNING")


@pytest.fixture
def tmp_config(tmp_path):
    """Default config with cache/outputs/logs redirected into a temporary folder."""
    return load_config(
        overrides={
            "cache": {"dir": str(tmp_path / "cache")},
            "output": {"dir": str(tmp_path / "outputs")},
            "logging": {"dir": str(tmp_path / "logs")},
        }
    )


@pytest.fixture
def clean_registry():
    """Let a test register throw-away stages without leaking them into other tests."""
    saved = dict(pipeline.REGISTRY)
    pipeline.REGISTRY.clear()
    yield pipeline.REGISTRY
    pipeline.REGISTRY.clear()
    pipeline.REGISTRY.update(saved)
