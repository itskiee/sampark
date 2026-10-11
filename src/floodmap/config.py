"""Configuration loading and validation.

Layers, later ones override earlier ones:
    configs/default.yaml  ->  event file `config_overrides`  ->  extra YAML files  ->  CLI/UI
`configs/default.yaml` is the single source of truth for every tunable value (each one is
commented there with the reason for its value). The pydantic models below have no
defaults of their own, so the YAML cannot silently drift from the code, and
`extra="forbid"` turns a typo in a config key into a clear error instead of an ignored
setting.

Secrets are NOT configuration: they come only from environment variables / `.env`
(see `load_settings`) and are held as `SecretStr`, which never prints its value.
"""

from __future__ import annotations

import copy
import os
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from floodmap.logging_setup import register_secret

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "configs" / "default.yaml"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------------------------------------------------------------------------
# Pipeline configuration (non-secret, tunable)
# ---------------------------------------------------------------------------
class LimitsConfig(_Strict):
    max_aoi_km2: float = Field(gt=0)
    min_aoi_km2: float = Field(gt=0)


class CacheConfig(_Strict):
    enabled: bool
    dir: Path


class OutputConfig(_Strict):
    dir: Path


class LoggingConfig(_Strict):
    dir: Path
    level: str

    @field_validator("level")
    @classmethod
    def _valid_level(cls, v: str) -> str:
        if v.upper() not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise ValueError("level must be DEBUG, INFO, WARNING or ERROR")
        return v.upper()


class NetworkConfig(_Strict):
    timeout_s: float = Field(gt=0)
    retries: int = Field(ge=0, le=10)


class PipelineConfig(_Strict):
    limits: LimitsConfig
    cache: CacheConfig
    output: OutputConfig
    logging: LoggingConfig
    network: NetworkConfig

    def resolve(self, path: Path) -> Path:
        """Relative paths in the config are relative to the repository root, so the
        result does not depend on the directory the CLI or dashboard was started from."""
        return path if path.is_absolute() else REPO_ROOT / path


# ---------------------------------------------------------------------------
# Event specification (what to analyse)
# ---------------------------------------------------------------------------
BBox = tuple[float, float, float, float]


class AOISpec(_Strict):
    """Area of interest: exactly one of a lon/lat bbox or a GeoJSON file."""

    bbox: BBox | None = None
    geojson: Path | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> AOISpec:
        if (self.bbox is None) == (self.geojson is None):
            raise ValueError("AOI needs exactly one of 'bbox' or 'geojson'")
        return self


class EventSpec(_Strict):
    name: str = Field(min_length=1, max_length=200)
    event_date: date
    aoi: AOISpec
    focus_bbox: BBox | None = None
    config_overrides: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
class ConfigError(ValueError):
    """Invalid or missing configuration, with a message meant for the user."""


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge `override` into a copy of `base` (dicts merge, other values replace)."""
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)  # safe_load: never executes YAML tags
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"Config file must contain a mapping at top level: {path}")
    return data


def load_event(path: Path) -> EventSpec:
    """Load and validate an event file (e.g. configs/events/trishuli_2026.yaml)."""
    try:
        return EventSpec.model_validate(_read_yaml(path))
    except ValueError as exc:  # pydantic ValidationError is a ValueError
        raise ConfigError(f"Invalid event file {path}:\n{exc}") from exc


def load_config(
    event: EventSpec | None = None,
    extra_files: tuple[Path, ...] = (),
    overrides: dict[str, Any] | None = None,
) -> PipelineConfig:
    """Build the effective PipelineConfig from all layers."""
    data = _read_yaml(DEFAULT_CONFIG_PATH)
    if event is not None:
        data = deep_merge(data, event.config_overrides)
    for extra in extra_files:
        data = deep_merge(data, _read_yaml(extra))
    if overrides:
        data = deep_merge(data, overrides)
    try:
        return PipelineConfig.model_validate(data)
    except ValueError as exc:
        raise ConfigError(f"Invalid configuration:\n{exc}") from exc


# ---------------------------------------------------------------------------
# Secrets / environment
# ---------------------------------------------------------------------------
class Settings(BaseModel):
    """Values from the environment / .env. Secrets are SecretStr and never printed."""

    model_config = ConfigDict(frozen=True)

    llm_provider: str | None
    llm_api_key: SecretStr | None

    def summary(self) -> dict[str, str]:
        """Safe-to-print overview: says whether each value is set, never the secret itself."""
        return {
            "LLM_PROVIDER": self.llm_provider or "not set",
            "LLM_API_KEY": "set (hidden)" if self.llm_api_key else "not set",
        }


def _env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def load_settings(env_file: Path | None = None) -> Settings:
    """Read settings from the process environment, filling gaps from `.env`.

    Real environment variables win over `.env` (override=False), which is the usual
    convention and lets CI or a hosting platform inject values without a file.
    """
    load_dotenv(env_file or REPO_ROOT / ".env", override=False)
    api_key = _env("LLM_API_KEY")
    register_secret(api_key)
    return Settings(
        llm_provider=_env("LLM_PROVIDER"),
        llm_api_key=SecretStr(api_key) if api_key else None,
    )
