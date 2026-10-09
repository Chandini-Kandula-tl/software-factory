"""Factory configuration: loading, validation, and path resolution.

Configuration is YAML. Relative paths are resolved against the directory that
contains the config file, so the factory behaves the same regardless of the
shell's cwd.
Secrets are never stored here: cloud providers name an environment variable
(``api_key_env``) that is read only when that provider is enabled.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator

from software_factory.domain.errors import ConfigurationError

CONFIG_ENV_VAR = "FACTORY_CONFIG"
DEFAULT_CONFIG_RELATIVE = Path("config") / "factory.yaml"


class FactorySection(BaseModel):
    environment: str = "local"
    workspace: str = "./factory-workspace"
    prompts_dir: str = "./prompts"


class OrchestratorSection(BaseModel):
    type: Literal["langgraph"] = "langgraph"


class PersistenceSection(BaseModel):
    type: Literal["sqlite"] = "sqlite"


class ProviderConfig(BaseModel):
    type: str
    enabled: bool = True
    base_url: str | None = None
    api_key_env: str | None = None
    timeout_seconds: float = 300.0
    options: dict[str, Any] = Field(default_factory=dict)


class ModelConfig(BaseModel):
    provider: str
    model: str
    capabilities: list[str] = Field(default_factory=list)
    context_window: int | None = None
    options: dict[str, Any] = Field(default_factory=dict)


class ProfileConfig(BaseModel):
    preferred_model: str
    fallback_models: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    temperature: float = 0.0


class LimitsConfig(BaseModel):
    max_agent_retries: int = Field(default=2, ge=0, le=10)
    max_structured_output_repairs: int = Field(default=2, ge=0, le=5)
    max_clarification_rounds: int = Field(default=5, ge=1, le=20)
    max_debug_iterations: int = Field(default=5, ge=0, le=20)
    max_review_rounds: int = Field(default=2, ge=0, le=10)
    # Fix proposals tried per bug-fixing round; each is applied and really tested,
    # the first one that passes is kept (local models are inconsistent, retries are free).
    fix_candidates: int = Field(default=3, ge=1, le=5)
    # Budget of source-file characters given to build-phase agents per call.
    max_context_chars: int = Field(default=40_000, ge=2_000, le=500_000)
    retry_backoff_seconds: float = Field(default=1.0, ge=0)


class SecurityConfig(BaseModel):
    shell_enabled: bool = True
    # Run project commands with the user's normal PATH, without the factory's own
    # Python environment, so a generated app never depends on the factory's tools.
    isolate_project_environment: bool = True
    network_enabled: bool = False
    allowed_commands: list[str] = Field(
        default_factory=lambda: [
            "git",
            "python",
            "python3",
            "pip",
            "pytest",
            "ruff",
            "uv",
            "node",
            "npm",
            "npx",
        ]
    )
    command_timeout_seconds: float = 600.0


class LoggingConfig(BaseModel):
    level: str = "INFO"


class FactoryConfig(BaseModel):
    factory: FactorySection = Field(default_factory=FactorySection)
    orchestrator: OrchestratorSection = Field(default_factory=OrchestratorSection)
    persistence: PersistenceSection = Field(default_factory=PersistenceSection)
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    models: dict[str, ModelConfig] = Field(default_factory=dict)
    profiles: dict[str, ProfileConfig] = Field(default_factory=dict)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    # Directory relative paths resolve against. Not part of the YAML.
    base_dir: Path = Field(default_factory=Path.cwd, exclude=True)
    source_path: Path | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def _check_references(self) -> FactoryConfig:
        for name, model in self.models.items():
            if model.provider not in self.providers:
                raise ValueError(f"model '{name}' references unknown provider '{model.provider}'")
        for name, profile in self.profiles.items():
            for ref in [profile.preferred_model, *profile.fallback_models]:
                if ref not in self.models:
                    raise ValueError(f"profile '{name}' references unknown model '{ref}'")
        return self

    @property
    def workspace_path(self) -> Path:
        return _resolve(self.base_dir, self.factory.workspace)

    @property
    def prompts_path(self) -> Path:
        return _resolve(self.base_dir, self.factory.prompts_dir)

    def fingerprint(self) -> str:
        """Stable hash of the effective configuration (no secrets are in it)."""
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:12]


def _resolve(base: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def find_config_path(explicit: str | Path | None = None) -> Path | None:
    """Locate the config file: explicit path, $FACTORY_CONFIG, then ./config/factory.yaml."""
    if explicit:
        return Path(explicit).expanduser().resolve()
    env_value = os.environ.get(CONFIG_ENV_VAR)
    if env_value:
        return Path(env_value).expanduser().resolve()
    candidate = Path.cwd() / DEFAULT_CONFIG_RELATIVE
    return candidate.resolve() if candidate.exists() else None


def load_dotenv(path: Path) -> None:
    """Load ``KEY=value`` lines from a local .env file (spec §47).

    Variables already set in the environment win; values are never logged.
    """
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip().removeprefix("export ").strip(), value.strip().strip("'\"")
        if key and value:
            os.environ.setdefault(key, value)


def load_config(path: str | Path | None = None) -> FactoryConfig:
    load_dotenv(Path.cwd() / ".env")
    config_path = find_config_path(path)
    if config_path is None:
        raise ConfigurationError(
            "No factory configuration found",
            hint=f"Run from the repository root (which has {DEFAULT_CONFIG_RELATIVE}), "
            f"pass --config, or set {CONFIG_ENV_VAR}.",
        )
    if not config_path.exists():
        raise ConfigurationError(f"Configuration file not found: {config_path}")
    try:
        raw = yaml.safe_load(config_path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"Configuration file is not valid YAML: {exc}") from exc
    return config_from_dict(raw, base_dir=config_path.parent, source_path=config_path)


def config_from_dict(
    raw: dict[str, Any], base_dir: Path | None = None, source_path: Path | None = None
) -> FactoryConfig:
    try:
        config = FactoryConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigurationError(
            f"Invalid factory configuration: {exc.errors()[0]['msg']}",
            hint=str(exc),
        ) from exc
    config.base_dir = base_dir or Path.cwd()
    config.source_path = source_path
    return config
