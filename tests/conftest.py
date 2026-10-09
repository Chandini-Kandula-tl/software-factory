from __future__ import annotations

from pathlib import Path

import pytest

from software_factory.config.settings import FactoryConfig, config_from_dict
from software_factory.core.factory_service import FactoryService
from software_factory.llm.providers.base import register_provider_type
from tests.support.scripted_provider import SCRIPT, Script, ScriptedProvider

REPO_ROOT = Path(__file__).resolve().parents[1]

register_provider_type("scripted", ScriptedProvider)


def make_config(tmp_path: Path, provider_type: str = "scripted", **limits) -> FactoryConfig:
    return config_from_dict(
        {
            "factory": {
                "workspace": str(tmp_path / "workspace"),
                "prompts_dir": str(REPO_ROOT / "prompts"),
            },
            "providers": {"local": {"type": provider_type, "base_url": "http://ollama.test"}},
            "models": {
                "main": {
                    "provider": "local",
                    "model": "test-model",
                    "capabilities": ["reasoning", "coding", "structured_output"],
                }
            },
            "profiles": {
                "reasoning": {"preferred_model": "main"},
                "coding": {"preferred_model": "main"},
            },
            "limits": {"retry_backoff_seconds": 0, "fix_candidates": 1, **limits},
            # Tests run project commands with the test interpreter's `python`.
            "security": {"isolate_project_environment": False},
        },
        base_dir=tmp_path,
    )


@pytest.fixture
def script() -> Script:
    SCRIPT.queues.clear()
    SCRIPT.requests.clear()
    return SCRIPT


@pytest.fixture
def config(tmp_path: Path) -> FactoryConfig:
    return make_config(tmp_path)


@pytest.fixture
def factory(config: FactoryConfig, script: Script) -> FactoryService:
    return FactoryService(config)
