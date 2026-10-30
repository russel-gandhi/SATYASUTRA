"""Typed loader for config/*.yaml. Nothing that can change between machines or runs is written in code (Bible D4)."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .errors import ConfigError

DEFAULT_CONFIG_DIR_ENV = "FORGE_CONFIG_DIR"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceConfig(_Strict):
    id: str
    role: str
    description: str = ""
    probe_url: str
    tier: Literal[1, 2, 3, 4]
    auth_env: str | None = None          # name of the environment variable holding the key, never the key
    auth_param: str | None = None        # query parameter that carries the key, when it goes in the URL
    rate_limit_per_min: int | None = Field(default=None, gt=0)
    cache_ttl_s: int = Field(default=0, ge=0)
    status: Literal["unverified", "reachable", "unreachable", "removed"] = "unverified"
    note: str = ""

    @field_validator("probe_url")
    @classmethod
    def _http(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):  # forge: allow-literal scheme check, not a source
            raise ValueError("probe_url must be an http(s) URL")
        return v


class SourcesFile(_Strict):
    sources: list[SourceConfig]

    @field_validator("sources")
    @classmethod
    def _unique(cls, v: list[SourceConfig]) -> list[SourceConfig]:
        ids = [s.id for s in v]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate source ids: {sorted(dupes)}")
        return v


class Budgets(_Strict):
    max_steps_per_claim: int = Field(gt=0)
    max_seconds_per_claim: int = Field(gt=0)
    max_fetches_per_claim: int = Field(gt=0)
    reverse_search_per_day: int = Field(ge=0)


class GeminiPolicy(_Strict):
    api_base: str
    models: list[str] = Field(min_length=1)
    measure_max_calls: int = Field(gt=0)
    measure_timeout_s: int = Field(gt=0)
    retest_attempts: int = Field(gt=0)
    requests_per_minute: int | None = None      # filled from docs/gemini_limits.md after a measurement
    measured_on: str = ""


class PolicyFile(_Strict):
    allowed_domains: list[str]
    read_only: bool = True
    budgets: Budgets
    gemini: GeminiPolicy


class ThresholdEntry(_Strict):
    value: float
    model_sha256: str = ""               # empty until a model exists
    calibrated_on: str = ""
    date: str = ""


class ThresholdsFile(_Strict):
    thresholds: dict[str, ThresholdEntry] = Field(default_factory=dict)


class ProbeSettings(_Strict):
    timeout_s: float = Field(gt=0)
    user_agent: str


class IngestSettings(_Strict):
    source_id: str
    ws_url: str
    collections: list[str] = Field(min_length=1)
    post_url_template: str
    langs: list[str] = Field(default_factory=list)       # empty means every language
    keywords: list[str] = Field(default_factory=list)    # empty means no keyword filter
    min_text_chars: int = Field(ge=0)
    skip_replies: bool
    open_timeout_s: float = Field(gt=0)
    idle_timeout_s: float = Field(gt=0)
    reconnect_delay_s: float = Field(gt=0)
    max_reconnect_delay_s: float = Field(gt=0)
    resume_overlap_s: float = Field(ge=0)
    recent_ids_kept: int = Field(gt=0)


class RuntimeFile(_Strict):
    ledger_db_path: str
    probe: ProbeSettings
    ingest: IngestSettings


class Config(_Strict):
    sources: SourcesFile
    policy: PolicyFile
    thresholds: ThresholdsFile
    runtime: RuntimeFile
    checksum: str

    def source(self, source_id: str) -> SourceConfig:
        for s in self.sources.sources:
            if s.id == source_id:
                return s
        raise ConfigError(f"unknown source id {source_id!r}")


def _read_yaml(path: Path) -> dict:
    if not path.is_file():
        raise ConfigError(f"missing config file: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path.name}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name}: top level must be a mapping")
    return data


def _parse(model, path: Path):
    try:
        return model.model_validate(_read_yaml(path))
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        raise ConfigError(f"{path.name}: {problems}") from exc


def config_dir(override: str | Path | None = None) -> Path:
    if override is not None:
        return Path(override)
    env = os.environ.get(DEFAULT_CONFIG_DIR_ENV)
    return Path(env) if env else Path(__file__).resolve().parents[2] / "config"


def load_config(directory: str | Path | None = None) -> Config:
    base = config_dir(directory)
    names = ("sources", "policy", "thresholds", "runtime")
    digest = hashlib.sha256()
    for name in names:
        digest.update((base / f"{name}.yaml").read_bytes() if (base / f"{name}.yaml").is_file() else b"")
    return Config(
        sources=_parse(SourcesFile, base / "sources.yaml"),
        policy=_parse(PolicyFile, base / "policy.yaml"),
        thresholds=_parse(ThresholdsFile, base / "thresholds.yaml"),
        runtime=_parse(RuntimeFile, base / "runtime.yaml"),
        checksum=digest.hexdigest(),
    )


def get_secret(env_name: str) -> str:
    """Secrets come only from the environment. A missing one is an error, never an empty string."""
    value = os.environ.get(env_name)
    if not value:
        raise ConfigError(f"environment variable {env_name} is not set")
    return value


def has_secret(env_name: str | None) -> bool:
    return bool(env_name and os.environ.get(env_name))
