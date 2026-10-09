"""Lifecycle state machine, quality gates, configuration, redaction, prompts."""

from __future__ import annotations

from pathlib import Path

import pytest

from software_factory.agents.prompts import PromptLibrary
from software_factory.config.settings import config_from_dict, load_config
from software_factory.domain.errors import (
    ConfigurationError,
    InvalidProjectIdError,
    InvalidTransitionError,
    QualityGateError,
)
from software_factory.domain.lifecycle import LifecycleStatus as S
from software_factory.domain.lifecycle import transition
from software_factory.domain.project import slugify_project_name, validate_project_id
from software_factory.domain.quality_gates import require_entry, unmet_entry_criteria
from software_factory.llm.providers.base import create_provider, read_api_key
from software_factory.observability.redaction import REDACTED, redact

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_valid_transitions():
    assert transition(S.CREATED, S.REQUIREMENTS_ANALYSIS) is S.REQUIREMENTS_ANALYSIS
    assert transition("REQUIREMENTS_ANALYSIS", S.FAILED) is S.FAILED
    assert transition(S.FAILED, S.ARCHITECTURE_ANALYSIS) is S.ARCHITECTURE_ANALYSIS


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.CREATED, S.ARCHITECTURE_ANALYSIS),  # cannot skip requirements
        (S.WAITING_FOR_REQUIREMENT_APPROVAL, S.TECHNOLOGY_SELECTION),
        (S.REQUIREMENTS_ANALYSIS, S.READY_FOR_DEVELOPMENT),
        (S.WAITING_FOR_ARCHITECTURE_APPROVAL, S.FAILED),  # gates don't fail, stages do
        (S.CANCELLED, S.REQUIREMENTS_ANALYSIS),
    ],
)
def test_invalid_transitions(current, target):
    with pytest.raises(InvalidTransitionError):
        transition(current, target)


def test_quality_gates():
    state = {"requirements_input": "x", "approvals": {}}
    assert unmet_entry_criteria("requirements", state) == []
    with pytest.raises(QualityGateError, match="requirements are approved"):
        require_entry("architecture", state)
    state["approvals"] = {"requirements": {"status": "rejected"}}
    assert unmet_entry_criteria("architecture", state) == [
        "requirements are approved",
        "requirements artifact exists",
    ]
    state["approvals"] = {
        s: {"status": "approved"}
        for s in ("requirements", "architecture", "technology", "implementation_plan")
    }
    assert unmet_entry_criteria("development", state) == ["implementation plan exists"]
    state["implementation_plan"] = {"tasks": []}
    assert unmet_entry_criteria("development", state) == []


def test_project_ids():
    assert slugify_project_name("Employee Management!") == "employee-management"
    for bad in ("../x", "A", "", "-x", "a/b"):
        with pytest.raises(InvalidProjectIdError):
            validate_project_id(bad)


def test_repository_config_loads_and_resolves_paths():
    config = load_config(REPO_ROOT / "config" / "factory.yaml")
    assert config.prompts_path == REPO_ROOT / "prompts"
    assert config.workspace_path == REPO_ROOT / "factory-workspace"
    assert config.profiles["reasoning"].preferred_model in config.models
    assert len(config.fingerprint()) == 12


def test_config_rejects_unknown_references_and_bad_yaml(tmp_path):
    with pytest.raises(ConfigurationError, match="unknown provider"):
        config_from_dict({"models": {"m": {"provider": "nope", "model": "x"}}})
    with pytest.raises(ConfigurationError, match="unknown model"):
        config_from_dict({"profiles": {"reasoning": {"preferred_model": "missing"}}})
    bad = tmp_path / "factory.yaml"
    bad.write_text("providers: [unclosed")
    with pytest.raises(ConfigurationError, match="YAML"):
        load_config(bad)
    with pytest.raises(ConfigurationError, match="not found"):
        load_config(tmp_path / "missing.yaml")


def test_config_env_var(tmp_path, monkeypatch):
    path = tmp_path / "custom.yaml"
    path.write_text("factory:\n  workspace: ws\n")
    monkeypatch.setenv("FACTORY_CONFIG", str(path))
    assert load_config().workspace_path == tmp_path / "ws"


def test_unimplemented_provider_type_is_actionable():
    from software_factory.config.settings import ProviderConfig

    with pytest.raises(ConfigurationError) as exc:
        create_provider("mystery", ProviderConfig(type="carrier_pigeon"))
    assert "ollama" in exc.value.hint


def test_api_key_read_only_from_environment(monkeypatch):
    from software_factory.config.settings import ProviderConfig

    cfg = ProviderConfig(type="x", api_key_env="DEEPSEEK_API_KEY")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ConfigurationError, match="DEEPSEEK_API_KEY"):
        read_api_key("deepseek", cfg)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "value-123456")
    assert read_api_key("deepseek", cfg) == "value-123456"


def test_redaction(monkeypatch):
    monkeypatch.setenv("MY_SERVICE_TOKEN", "super-secret-token-value")
    text = (
        "key sk-abcdefghijklmnopqrstuvwxyz0123 aws AKIAABCDEFGHIJKLMNOP "
        "password=hunter2hunter Authorization: Bearer abc.def.ghijklmnopqrstu "
        "env super-secret-token-value"
    )
    cleaned = redact(text)
    for secret in (
        "sk-abcdefghijklmnopqrstuvwxyz0123",
        "AKIAABCDEFGHIJKLMNOP",
        "hunter2hunter",
        "abc.def.ghijklmnopqrstu",
        "super-secret-token-value",
    ):
        assert secret not in cleaned
    assert REDACTED in cleaned
    assert redact("nothing secret here") == "nothing secret here"


def test_prompts_are_versioned():
    library = PromptLibrary(REPO_ROOT / "prompts")
    prompt = library.load("requirement", "system.md")
    assert prompt.version.startswith("1.0.0+") and len(prompt.version.split("+")[1]) == 8
    task = library.load("requirement", "task.md")
    rendered = task.render(project_name="Demo", requirements_input="Build X")
    assert "Demo" in rendered and "Build X" in rendered and "---" not in rendered.splitlines()[0]
    with pytest.raises(ConfigurationError):
        library.load("requirement", "missing.md")


def test_dotenv_loaded_without_overriding_environment(tmp_path, monkeypatch):
    from software_factory.config.settings import load_dotenv

    env = tmp_path / ".env"
    env.write_text("# comment\nSF_TEST_A=from-file\nexport SF_TEST_B='quoted'\nSF_TEST_C=file\n")
    monkeypatch.delenv("SF_TEST_A", raising=False)
    monkeypatch.delenv("SF_TEST_B", raising=False)
    monkeypatch.setenv("SF_TEST_C", "shell")
    load_dotenv(env)
    import os

    assert os.environ["SF_TEST_A"] == "from-file"
    assert os.environ["SF_TEST_B"] == "quoted"
    assert os.environ["SF_TEST_C"] == "shell"
    for key in ("SF_TEST_A", "SF_TEST_B"):
        monkeypatch.delenv(key)
