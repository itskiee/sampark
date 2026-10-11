from __future__ import annotations

import pytest

from floodmap import pipeline
from floodmap.config import load_config


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
