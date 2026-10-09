"""``factory doctor`` — environment and model health checks with actionable messages."""

from __future__ import annotations

import importlib.metadata
import os
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel

from software_factory.config.settings import FactoryConfig
from software_factory.domain.errors import FactoryError
from software_factory.llm.models import Capability, LLMRequest, Message, MessageRole
from software_factory.llm.registry import ModelRegistry
from software_factory.llm.router import ModelRouter

REQUIRED_PACKAGES = ("langgraph", "langgraph-checkpoint-sqlite", "pydantic", "typer", "httpx")
# Profiles used by implemented agents: design agents use "reasoning", the
# Coding, Testing and Code Review agents use "coding". A profile FAILs only when
# none of its models (preferred or fallback) is installed.
ACTIVE_PROFILES = ("reasoning", "coding")
PROMPT_DIRS = (
    "requirement",
    "architecture",
    "technology_selection",
    "implementation_planning",
    "coding",
    "testing",
    "code_review",
    "impact_analysis",
)


class CheckStatus(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIP = "SKIP"


@dataclass
class CheckResult:
    name: str
    status: CheckStatus
    detail: str = ""
    hint: str = ""


class _ProbeAnswer(BaseModel):
    answer: int


def run_doctor(config: FactoryConfig, probe: bool = False) -> list[CheckResult]:
    results = [
        _check_python(),
        _check_dependencies(),
        _check_configuration(config),
        _check_prompts(config),
        _check_workspace(config),
        _check_database(config),
        _check_git(),
    ]
    registry = ModelRegistry(config)
    provider_results = _check_providers(config, registry)
    unreachable = {
        r.name.removeprefix("Provider: ") for r in provider_results if r.status is CheckStatus.FAIL
    }
    results += provider_results
    results += _check_models(config, registry, unreachable)
    if probe and not unreachable:
        results += _probe_profiles(config, registry)
    return results


def _check_python() -> CheckResult:
    # The package requires Python 3.11+, so reaching this code proves the minimum.
    return CheckResult("Python", CheckStatus.PASS, ".".join(map(str, sys.version_info[:3])))


def _check_dependencies() -> CheckResult:
    missing, found = [], []
    for package in REQUIRED_PACKAGES:
        try:
            found.append(f"{package} {importlib.metadata.version(package)}")
        except importlib.metadata.PackageNotFoundError:
            missing.append(package)
    if missing:
        return CheckResult(
            "Dependencies", CheckStatus.FAIL, f"missing: {', '.join(missing)}", "Run `uv sync`."
        )
    return CheckResult("Dependencies", CheckStatus.PASS, "; ".join(found))


def _check_configuration(config: FactoryConfig) -> CheckResult:
    if not config.profiles:
        return CheckResult(
            "Configuration",
            CheckStatus.FAIL,
            "no model profiles configured",
            "Define profiles (at least 'reasoning') in the config file.",
        )
    if "reasoning" not in config.profiles:
        return CheckResult(
            "Configuration",
            CheckStatus.FAIL,
            "profile 'reasoning' is missing",
            "The design-phase agents use the 'reasoning' profile.",
        )
    source = str(config.source_path) if config.source_path else "defaults"
    return CheckResult("Configuration", CheckStatus.PASS, f"{source} ({config.fingerprint()})")


def _check_prompts(config: FactoryConfig) -> CheckResult:
    missing = [
        f"{d}/{name}"
        for d in PROMPT_DIRS
        for name in ("system.md", "task.md")
        if not (config.prompts_path / d / name).exists()
    ]
    if missing:
        return CheckResult(
            "Prompts",
            CheckStatus.FAIL,
            f"missing: {', '.join(missing)}",
            f"Check factory.prompts_dir (currently {config.prompts_path}).",
        )
    return CheckResult("Prompts", CheckStatus.PASS, str(config.prompts_path))


def _check_workspace(config: FactoryConfig) -> CheckResult:
    workspace = config.workspace_path
    try:
        workspace.mkdir(parents=True, exist_ok=True)
        probe = workspace / ".doctor-write-test"
        probe.write_text("ok")
        probe.unlink()
    except OSError as exc:
        return CheckResult("Workspace", CheckStatus.FAIL, str(exc), "Check factory.workspace.")
    return CheckResult("Workspace", CheckStatus.PASS, str(workspace))


def _check_database(config: FactoryConfig) -> CheckResult:
    path = config.workspace_path / "factory.db"
    try:
        conn = sqlite3.connect(path)
        conn.execute("SELECT 1")
        conn.close()
    except sqlite3.Error as exc:
        return CheckResult("Database", CheckStatus.FAIL, str(exc))
    return CheckResult("Database", CheckStatus.PASS, f"SQLite {sqlite3.sqlite_version} at {path}")


def _check_git() -> CheckResult:
    if not shutil.which("git"):
        return CheckResult("Git", CheckStatus.FAIL, "git not found", "Install git.")
    out = subprocess.run(["git", "--version"], capture_output=True, text=True, check=False)
    return CheckResult("Git", CheckStatus.PASS, out.stdout.strip())


def _check_providers(config: FactoryConfig, registry: ModelRegistry) -> list[CheckResult]:
    results = []
    for name, provider_config in config.providers.items():
        label = f"Provider: {name}"
        if not provider_config.enabled:
            results.append(CheckResult(label, CheckStatus.SKIP, "disabled"))
            continue
        try:
            health = registry.provider(name).health_check()
        except FactoryError as exc:
            results.append(CheckResult(label, CheckStatus.FAIL, str(exc), exc.hint or ""))
            continue
        if health.healthy:
            results.append(CheckResult(label, CheckStatus.PASS, health.detail))
        else:
            results.append(
                CheckResult(
                    label,
                    CheckStatus.FAIL,
                    health.detail,
                    "See docs/models/local_model_setup.md",
                )
            )
    return results


def _usage_note(key: str, config: FactoryConfig) -> str:
    uses = [
        f"{'preferred' if config.profiles[p].preferred_model == key else 'fallback'} for {p}"
        for p in ACTIVE_PROFILES
        if p in config.profiles
        and key in [config.profiles[p].preferred_model, *config.profiles[p].fallback_models]
    ]
    return f" ({'; '.join(uses)})" if uses else " (not used by any profile)"


def _total_memory_bytes() -> int | None:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return None


def _check_models(
    config: FactoryConfig, registry: ModelRegistry, unreachable: set[str]
) -> list[CheckResult]:
    results = []
    memory = _total_memory_bytes()
    installed: set[str] = set()
    checked: set[str] = set()
    for key, model in config.models.items():
        label = f"Model: {key}"
        if not config.providers[model.provider].enabled:
            results.append(CheckResult(label, CheckStatus.SKIP, "provider disabled"))
            continue
        if model.provider in unreachable:
            results.append(
                CheckResult(label, CheckStatus.SKIP, f"{model.model}: provider unreachable")
            )
            continue
        try:
            metadata = registry.provider(model.provider).get_model_metadata(model.model)
        except FactoryError as exc:
            results.append(CheckResult(label, CheckStatus.FAIL, str(exc), exc.hint or ""))
            continue
        checked.add(key)
        if not metadata.available:
            results.append(
                CheckResult(
                    label,
                    CheckStatus.WARN,
                    f"{model.model} is not installed" + _usage_note(key, config),
                    (
                        f"Run `ollama pull {model.model}` or change models.{key}.model."
                        if config.providers[model.provider].type == "ollama"
                        else f"Check the model name against the provider's model list, "
                        f"or change models.{key}.model."
                    ),
                )
            )
            continue
        installed.add(key)
        facts = [
            model.model,
            metadata.parameter_size or "",
            metadata.quantization or "",
            f"ctx {metadata.context_length}" if metadata.context_length else "",
            f"caps: {', '.join(model.capabilities)}",
        ]
        detail = " · ".join(f for f in facts if f)
        status, hint = CheckStatus.PASS, ""
        if memory and metadata.size_bytes and metadata.size_bytes > 0.7 * memory:
            status = CheckStatus.WARN
            hint = (
                f"Model needs ~{metadata.size_bytes / 1e9:.1f} GB; machine has "
                f"{memory / 1e9:.1f} GB RAM. Choose a smaller model or quantization."
            )
        results.append(CheckResult(label, status, detail, hint))
    results += _check_profiles(config, installed, checked)
    return results


def _check_profiles(
    config: FactoryConfig, installed: set[str], checked: set[str]
) -> list[CheckResult]:
    """Each profile an implemented agent uses needs at least one installed model."""
    results = []
    for name in ACTIVE_PROFILES:
        label = f"Profile: {name}"
        profile = config.profiles.get(name)
        if profile is None:
            results.append(
                CheckResult(
                    label,
                    CheckStatus.FAIL,
                    "not configured",
                    f"Add a '{name}' profile to the config.",
                )
            )
            continue
        candidates = [profile.preferred_model, *profile.fallback_models]
        if not any(c in checked for c in candidates):
            continue  # provider unreachable or disabled; already reported
        usable = [c for c in candidates if c in installed]
        if not usable:
            results.append(
                CheckResult(
                    label,
                    CheckStatus.FAIL,
                    "no installed model",
                    "Pull one of: " + ", ".join(config.models[c].model for c in candidates),
                )
            )
        elif usable[0] == profile.preferred_model:
            results.append(
                CheckResult(label, CheckStatus.PASS, f"uses {config.models[usable[0]].model}")
            )
        else:
            results.append(
                CheckResult(
                    label,
                    CheckStatus.WARN,
                    f"preferred model missing; will use fallback {config.models[usable[0]].model}",
                    f"Pull {config.models[profile.preferred_model].model} for best results.",
                )
            )
    return results


def _probe_profiles(config: FactoryConfig, registry: ModelRegistry) -> list[CheckResult]:
    """Live check: ask each profile's model for a tiny structured answer."""
    router = ModelRouter(registry, config.limits.model_copy(update={"max_agent_retries": 0}))
    results = []
    for profile in (p for p in ACTIVE_PROFILES if p in config.profiles):
        label = f"Structured output ({profile})"
        if not router.supports(Capability.STRUCTURED_OUTPUT, profile):
            results.append(CheckResult(label, CheckStatus.SKIP, "profile has no structured_output"))
            continue
        request = LLMRequest(
            model_profile=profile,
            messages=[Message(role=MessageRole.USER, content="What is 2 + 3? Reply as JSON.")],
        )
        try:
            response = router.generate_structured(request, _ProbeAnswer)
        except FactoryError as exc:
            results.append(CheckResult(label, CheckStatus.FAIL, str(exc), exc.hint or ""))
            continue
        ok = response.parsed.answer == 5
        results.append(
            CheckResult(
                label,
                CheckStatus.PASS if ok else CheckStatus.WARN,
                f"{response.model} answered {response.parsed.answer} in {response.latency_ms} ms",
                ""
                if ok
                else "Model returned valid JSON but a wrong answer; consider a stronger model.",
            )
        )
    return results
