"""CLI end-to-end: the documented first-run path works through the real commands."""

from __future__ import annotations

from pathlib import Path

import httpx
import respx
import yaml
from typer.testing import CliRunner

from software_factory.cli.main import app
from tests.support import fixtures as fx

REPO_ROOT = Path(__file__).resolve().parents[2]
runner = CliRunner()


def _config_file(tmp_path: Path, provider_type: str = "scripted") -> Path:
    path = tmp_path / "config" / "factory.yaml"
    path.parent.mkdir()
    path.write_text(
        yaml.safe_dump(
            {
                "factory": {"workspace": "../ws", "prompts_dir": str(REPO_ROOT / "prompts")},
                "providers": {"local": {"type": provider_type, "base_url": "http://ollama.test"}},
                "models": {
                    "m": {
                        "provider": "local",
                        "model": "test-model",
                        "capabilities": ["reasoning", "coding", "structured_output"],
                    }
                },
                "profiles": {
                    "reasoning": {"preferred_model": "m"},
                    "coding": {"preferred_model": "m"},
                },
                "limits": {"retry_backoff_seconds": 0},
            }
        )
    )
    return path


def _invoke(config: Path, *args: str):
    # Wide terminal so Rich tables don't wrap the values we assert on.
    return runner.invoke(app, ["--config", str(config), *args], env={"COLUMNS": "200"})


def test_cli_design_flow(tmp_path, script):
    script.add(
        fx.REQUIREMENT_AGENT, fx.requirements_needing_clarification(), fx.clear_requirements()
    )
    script.add(fx.ARCHITECTURE_AGENT, fx.architecture())
    config = _config_file(tmp_path)
    requirements = tmp_path / "requirements.md"
    requirements.write_text(fx.EMPLOYEE_REQUIREMENTS)

    result = _invoke(
        config, "create", "Employee Management", "--requirements-file", str(requirements)
    )
    assert result.exit_code == 0, result.output
    assert "employee-management" in result.output

    result = _invoke(config, "start", "employee-management")
    assert result.exit_code == 0, result.output
    assert "WAITING_FOR_REQUIREMENT_CLARIFICATION" in result.output
    assert "Q1" in result.output

    result = _invoke(config, "continue", "employee-management")
    assert result.exit_code == 1
    assert "unanswered" in result.output

    assert _invoke(config, "answer", "employee-management", "Q1", "Web").exit_code == 0
    result = _invoke(config, "answer", "employee-management", "Q2", "Admins only")
    assert "All required questions answered" in result.output

    result = _invoke(config, "continue", "employee-management")
    assert result.exit_code == 0, result.output
    assert "WAITING_FOR_REQUIREMENT_APPROVAL" in result.output
    assert "requirements.md" in result.output

    result = _invoke(config, "approve", "employee-management")
    assert "WAITING_FOR_ARCHITECTURE_APPROVAL" in result.output

    result = _invoke(config, "status", "employee-management")
    assert "requirements, repository_analysis" in result.output

    assert _invoke(config, "history", "employee-management").exit_code == 0
    result = _invoke(config, "history", "employee-management", "--json")
    assert result.exit_code == 0 and '"requirement-agent"' in result.output

    result = _invoke(config, "list")
    assert "WAITING_FOR_ARCHITECTURE_APPROVAL" in result.output

    result = _invoke(config, "pause", "employee-management")
    assert "(paused)" in result.output


def test_cli_errors_are_actionable(tmp_path, script):
    config = _config_file(tmp_path)
    result = _invoke(config, "status", "nope")
    assert result.exit_code == 1
    assert "does not exist" in result.output and "factory list" in result.output
    result = runner.invoke(app, ["--config", str(tmp_path / "missing.yaml"), "list"])
    assert result.exit_code == 1 and "not found" in result.output


@respx.mock
def test_doctor_reports_model_server_state(tmp_path):
    config = _config_file(tmp_path, provider_type="ollama")
    respx.get("http://ollama.test/api/tags").mock(side_effect=httpx.ConnectError("refused"))
    result = _invoke(config, "doctor")
    assert result.exit_code == 1
    assert "Python" in result.output and "PASS" in result.output
    assert "Cannot reach Ollama" in result.output

    respx.get("http://ollama.test/api/tags").mock(
        return_value=httpx.Response(
            200,
            json={
                "models": [
                    {"name": "test-model", "size": 1000, "details": {"parameter_size": "7B"}}
                ]
            },
        )
    )
    respx.post("http://ollama.test/api/show").mock(
        return_value=httpx.Response(200, json={"model_info": {}})
    )
    respx.post("http://ollama.test/api/chat").mock(
        return_value=httpx.Response(200, json={"message": {"content": '{"answer": 5}'}})
    )
    result = _invoke(config, "doctor", "--probe")
    assert result.exit_code == 0, result.output
    assert "FAIL" not in result.output
    assert "answered 5" in result.output


def test_version(tmp_path):
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0 and "software-factory" in result.output


def test_cli_delete(tmp_path, script):
    config = _config_file(tmp_path)
    assert _invoke(config, "create", "Temp").exit_code == 0
    result = _invoke(config, "delete", "temp", "--yes")
    assert result.exit_code == 0 and "Deleted project" in result.output
    assert "temp" not in _invoke(config, "list").output


def test_cli_update_needs_a_completed_project(tmp_path, script):
    config = _config_file(tmp_path)
    requirements = tmp_path / "r.md"
    requirements.write_text("A calculator.")
    assert _invoke(config, "create", "Calc", "-f", str(requirements)).exit_code == 0
    result = _invoke(config, "update", "calc")
    assert result.exit_code == 1 and "--text" in result.output
    result = _invoke(config, "update", "calc", "--text", "Add multiply")
    assert result.exit_code == 1 and "Only a completed project" in result.output
