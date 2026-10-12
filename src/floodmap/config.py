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


class S1Config(_Strict):
    pre_window_days: int = Field(gt=0, le=120)
    post_window_days: int = Field(gt=0, le=120)
    max_pre_images: int = Field(ge=1, le=10)
    min_coverage: float = Field(gt=0, le=1)
    include_event_day_as_post: bool
    same_pass_minutes: float = Field(gt=0, le=60)
    resolution_m: float = Field(ge=10, le=200)
    max_pixels: int = Field(gt=10_000)
    speckle_window_px: int = Field(ge=1, le=15)
    min_valid_db: float
    sync_timeout_s: float = Field(gt=0)
    threshold_method: str
    decrease_db: float = Field(lt=0)
    increase_db: float = Field(gt=0)
    water_post_vv_db: float
    otsu_decrease_bounds_db: tuple[float, float]
    otsu_increase_bounds_db: tuple[float, float]
    otsu_min_separability: float = Field(ge=0, le=1)
    min_mapping_unit_m2: float = Field(ge=0)
    max_tracks: int = Field(ge=1, le=2)

    @field_validator("threshold_method")
    @classmethod
    def _valid_method(cls, v: str) -> str:
        if v not in {"fixed", "otsu"}:
            raise ValueError("threshold_method must be 'fixed' or 'otsu'")
        return v


class TerrainConfig(_Strict):
    dem_resolution_m: float = Field(ge=10, le=200)
    buffer_m: float = Field(ge=0, le=50_000)
    stream_area_km2: float = Field(gt=0)
    hand_max_m: float = Field(gt=0)
    slope_max_deg: float = Field(gt=0, le=90)
    permanent_water_vv_db: float
    permanent_water_vh_db: float


class S2Config(_Strict):
    pre_window_days: int = Field(gt=0, le=365)
    post_window_days: int = Field(gt=0, le=120)
    max_tile_cloud_pct: float = Field(ge=0, le=100)
    min_clear_share: float = Field(ge=0, le=1)
    water_mndwi: float = Field(ge=-1, le=1)
    ndvi_drop: float = Field(gt=0, le=2)
    ndvi_post_max: float = Field(ge=-1, le=1)


class ConfidenceConfig(_Strict):
    neighbourhood_px: int = Field(ge=0, le=5)
    medium_min_patch_m2: float = Field(ge=0)


class FloodPathConfig(_Strict):
    snap_radius_m: float = Field(ge=0)
    settlement_buffer_m: float = Field(gt=0)
    corridor_buffer_m: float = Field(gt=0)


class OSMConfig(_Strict):
    network_buffer_m: float = Field(ge=0, le=50_000)
    history_window_hours: float = Field(gt=0, le=24 * 31)
    max_tile_km2_buildings: float = Field(gt=0)
    max_tile_km2_sparse: float = Field(gt=0)
    timeout_s: float = Field(gt=0)
    max_text_length: int = Field(ge=10, le=500)
    settlement_radius_m: float = Field(gt=0)
    settlement_link_m: float = Field(gt=0)
    settlement_min_buildings: int = Field(ge=1)
    settlement_max_extent_m: float = Field(gt=0)
    settlement_near_radius_m: float = Field(gt=0)
    motorable_highways: tuple[str, ...]


class DamageConfig(_Strict):
    building_buffer_m: float = Field(ge=0)
    road_buffer_m: float = Field(ge=0)
    facility_buffer_m: float = Field(ge=0)
    bridge_approach_m: float = Field(ge=0)
    bridge_reach_m: float = Field(ge=0)
    max_listed: int = Field(ge=1, le=1000)


class CutoffConfig(_Strict):
    break_min_confidence: str
    snap_max_m: float = Field(gt=0)
    densify_m: float = Field(gt=0)
    boundary_margin_m: float = Field(ge=0)
    town_places: tuple[str, ...]
    detour_factor: float = Field(gt=1)
    detour_min_extra_km: float = Field(ge=0)
    max_listed: int = Field(ge=1, le=1000)

    @field_validator("break_min_confidence")
    @classmethod
    def _valid_conf(cls, v: str) -> str:
        if v not in {"low", "medium", "high"}:
            raise ValueError("break_min_confidence must be low, medium or high")
        return v


class PipelineConfig(_Strict):
    limits: LimitsConfig
    cache: CacheConfig
    output: OutputConfig
    logging: LoggingConfig
    s1: S1Config
    terrain: TerrainConfig
    s2: S2Config
    confidence: ConfidenceConfig
    flood_path: FloodPathConfig
    osm: OSMConfig
    damage: DamageConfig
    cutoff: CutoffConfig
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
    flood_path_start: tuple[float, float] | None = None  # (lon, lat) upstream point
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
