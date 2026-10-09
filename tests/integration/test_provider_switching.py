"""Spec §68.2: the same agents and workflow run on a different provider by config only.

The workflow runs once against the Ollama adapter (HTTP mocked, exactly as a
local server would answer) and once against a different provider type. No
agent, workflow or state code changes between the runs — only the config.
"""

from __future__ import annotations

import json

import httpx
import respx

from software_factory.core.factory_service import FactoryService
from tests.conftest import make_config
from tests.support import fixtures as fx

OUTPUT_BY_AGENT = {
    "requirement-agent": fx.clear_requirements(),
    "architecture-agent": fx.architecture(),
}


def _agent_from_prompt(body: dict) -> str:
    system = body["messages"][0]["content"]
    if "Requirement Agent" in system:
        return "requirement-agent"
    if "Architecture Agent" in system:
        return "architecture-agent"
    raise AssertionError("unexpected agent")


def _run_design_steps(factory: FactoryService) -> str:
    pid = factory.create_project("switching", fx.EMPLOYEE_REQUIREMENTS).project_id
    factory.start(pid)
    status = factory.approve(pid)
    assert status.lifecycle_status == "WAITING_FOR_ARCHITECTURE_APPROVAL"
    return pid


@respx.mock
def test_workflow_runs_on_ollama_adapter(tmp_path, script):
    def reply(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        assert body["model"] == "test-model"
        assert "format" in body  # structured output requested via JSON schema
        output = OUTPUT_BY_AGENT[_agent_from_prompt(body)]
        return httpx.Response(
            200,
            json={
                "message": {"content": json.dumps(output)},
                "prompt_eval_count": 100,
                "eval_count": 50,
            },
        )

    respx.post("http://ollama.test/api/chat").mock(side_effect=reply)
    factory = FactoryService(make_config(tmp_path, provider_type="ollama"))
    pid = _run_design_steps(factory)
    runs = [h for h in factory.history(pid) if h["kind"] == "agent"]
    assert {h["provider"] for h in runs} == {"local"}
    assert runs[0]["llm_calls"][0]["input_tokens"] == 100


def test_same_workflow_runs_on_another_provider_type(tmp_path, script):
    script.add("requirement-agent", fx.clear_requirements())
    script.add("architecture-agent", fx.architecture())
    factory = FactoryService(make_config(tmp_path, provider_type="scripted"))
    _run_design_steps(factory)
    assert len(script.requests) == 2


@respx.mock
def test_same_workflow_runs_on_an_openai_compatible_api(tmp_path, script, monkeypatch):
    """Switching to a cloud/OpenAI-compatible API is a config change only."""
    from software_factory.config.settings import config_from_dict
    from tests.conftest import REPO_ROOT

    monkeypatch.setenv("CLOUD_KEY", "sk-cloud-0123456789abcdefghij")

    def reply(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"].startswith("Bearer sk-cloud")
        body = json.loads(request.read())
        output = OUTPUT_BY_AGENT[_agent_from_prompt(body)]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(output)}}]})

    respx.post("https://api.cloud.test/v1/chat/completions").mock(side_effect=reply)
    config = config_from_dict(
        {
            "factory": {
                "workspace": str(tmp_path / "workspace"),
                "prompts_dir": str(REPO_ROOT / "prompts"),
            },
            "providers": {
                "cloud": {
                    "type": "openai_compatible",
                    "base_url": "https://api.cloud.test/v1",
                    "api_key_env": "CLOUD_KEY",
                }
            },
            "models": {
                "main": {
                    "provider": "cloud",
                    "model": "cloud-model",
                    "capabilities": ["reasoning", "coding", "structured_output"],
                }
            },
            "profiles": {"reasoning": {"preferred_model": "main"}},
            "limits": {"retry_backoff_seconds": 0},
        },
        base_dir=tmp_path,
    )
    factory = FactoryService(config)
    pid = _run_design_steps(factory)
    runs = [h for h in factory.history(pid) if h["kind"] == "agent"]
    assert {(h["provider"], h["model"]) for h in runs} == {("cloud", "cloud-model")}
    workspace = tmp_path / "workspace"
    for path in workspace.rglob("*"):
        if path.is_file() and path.suffix in {".md", ".json", ".log"}:
            assert "sk-cloud-0123456789abcdefghij" not in path.read_text()
