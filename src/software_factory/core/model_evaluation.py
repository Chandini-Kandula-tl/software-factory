"""``factory evaluate`` — is the configured model good enough for this factory?

Spec §68.17: not a general benchmark. Each case runs a *real factory agent*
(same prompts, same schemas, same model routing) on a small fixed task and
checks the answer with deterministic code. The code-generation case writes
the generated files to a temporary folder and runs the generated tests for
real, so "pass" means an exit code of 0, not the model's opinion.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from software_factory.agents.architecture import ArchitectureAgent
from software_factory.agents.base import LLMAgent
from software_factory.agents.coding import CodingAgent
from software_factory.agents.prompts import PromptLibrary
from software_factory.agents.requirement import RequirementAgent
from software_factory.agents.technology_selection import TechnologySelectionAgent
from software_factory.config.settings import FactoryConfig
from software_factory.domain.architecture import ArchitectureProposal
from software_factory.domain.development import CodeChanges, FileAction
from software_factory.domain.errors import FactoryError
from software_factory.domain.requirements import RequirementAnalysis
from software_factory.domain.technology import TechnologySelection
from software_factory.llm.interfaces import LLMClient
from software_factory.observability.clock import utc_now
from software_factory.tools.filesystem import FilesystemTool
from software_factory.tools.shell import ShellTool

CLEAR_REQUIREMENTS = (
    "Build a command-line tool that stores personal notes in a local file.\n"
    "Users can add a note, list all notes and delete a note by its number.\n"
    "Listing must finish within one second for up to 10,000 notes.\n"
    "It runs on the user's own laptop; no network access, no accounts."
)
VAGUE_REQUIREMENTS = "Build an app for managing things."
SMALL_REQUIREMENTS = {
    "summary": "Command-line notes tool storing notes in a local file.",
    "application_type": "command-line tool",
    "functional_requirements": [
        {"id": "FR-1", "description": "Add a note"},
        {"id": "FR-2", "description": "List all notes"},
        {"id": "FR-3", "description": "Delete a note by number"},
    ],
    "non_functional_requirements": [
        {"id": "NFR-1", "description": "Listing finishes within 1 second for 10,000 notes"}
    ],
    "constraints": ["Runs locally, no network, no accounts"],
}
PYTHON_ONLY_ENVIRONMENT = {
    "operating_system": "evaluation",
    "allowed_commands": ["python", "git"],
    "installed_commands": ["python", "git"],
    "not_installed": ["node", "npm", "docker", "java"],
}
NON_PYTHON_STACK_WORDS = ("node", "react", "java ", "spring", "docker", ".net", "golang")


@dataclass
class CaseResult:
    case: str
    capability: str
    passed: bool
    duration_s: float
    detail: str
    model: str = ""


@dataclass
class EvaluationReport:
    started_at: str
    results: list[CaseResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(r.passed for r in self.results)

    def as_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "passed": self.passed,
            "total": len(self.results),
            "results": [r.__dict__ for r in self.results],
        }


@dataclass(frozen=True)
class EvaluationCase:
    name: str
    capability: str
    run: Callable[[LLMClient, PromptLibrary], tuple[bool, str, str]]


def _context(agent: LLMAgent[Any], inputs: dict[str, Any]) -> Any:
    return agent.build_context("model-evaluation", "Evaluation project", "evaluation", inputs)


def _requirement_extraction(llm: LLMClient, prompts: PromptLibrary) -> tuple[bool, str, str]:
    agent = RequirementAgent(llm, prompts)
    result = agent.run(_context(agent, {"requirements_input": CLEAR_REQUIREMENTS}))
    output: RequirementAnalysis = result.output
    problems = []
    if len(output.functional_requirements) < 3:
        problems.append(
            f"expected ≥3 functional requirements, got {len(output.functional_requirements)}"
        )
    nfr_text = " ".join(r.description.lower() for r in output.non_functional_requirements)
    if "second" not in nfr_text and "1 s" not in nfr_text:
        problems.append("the one-second performance requirement was not captured as an NFR")
    detail = "; ".join(problems) or (
        f"{len(output.functional_requirements)} FR, {len(output.non_functional_requirements)} NFR"
    )
    return not problems, detail, result.model


def _ambiguity_detection(llm: LLMClient, prompts: PromptLibrary) -> tuple[bool, str, str]:
    agent = RequirementAgent(llm, prompts)
    result = agent.run(_context(agent, {"requirements_input": VAGUE_REQUIREMENTS}))
    output: RequirementAnalysis = result.output
    required = [q for q in output.clarification_questions if q.required]
    ok = bool(required)
    detail = (
        f"asked {len(required)} required question(s)"
        if ok
        else "did not ask any required clarification question for a vague request"
    )
    return ok, detail, result.model


def _architecture_reasoning(llm: LLMClient, prompts: PromptLibrary) -> tuple[bool, str, str]:
    agent = ArchitectureAgent(llm, prompts)
    result = agent.run(_context(agent, {"requirements": SMALL_REQUIREMENTS}))
    output: ArchitectureProposal = result.output
    mapped = {rid for c in output.components for rid in c.requirement_ids}
    missing = sorted({"FR-1", "FR-2", "FR-3"} - mapped)
    problems = []
    if len(output.components) < 2:
        problems.append("fewer than 2 components")
    if missing:
        problems.append(f"requirements not mapped to a component: {missing}")
    if not output.alternatives:
        problems.append("no alternative considered")
    detail = "; ".join(problems) or f"{len(output.components)} components, all FRs mapped"
    return not problems, detail, result.model


def _technology_fit(llm: LLMClient, prompts: PromptLibrary) -> tuple[bool, str, str]:
    agent = TechnologySelectionAgent(llm, prompts)
    architecture = {
        "style": "single-process command-line application",
        "overview": "A CLI that reads and writes a local file.",
        "components": [{"name": "CLI", "responsibility": "commands and storage"}],
    }
    result = agent.run(
        _context(
            agent,
            {
                "requirements": SMALL_REQUIREMENTS,
                "architecture": architecture,
                "environment": PYTHON_ONLY_ENVIRONMENT,
            },
        )
    )
    output: TechnologySelection = result.output
    chosen = " ".join(f"{c.choice} " for c in output.choices).lower()
    unfit = [w.strip() for w in NON_PYTHON_STACK_WORDS if w in chosen]
    ok = not unfit and all(c.rationale.strip() for c in output.choices)
    detail = (
        f"chose: {', '.join(c.choice for c in output.choices)}"
        if ok
        else f"chose technology that is not installed or needs infrastructure: {unfit}"
    )
    return ok, detail, result.model


def _code_generation(llm: LLMClient, prompts: PromptLibrary) -> tuple[bool, str, str]:
    agent = CodingAgent(llm, prompts)
    task = {
        "id": "T-1",
        "title": "Temperature conversion module",
        "description": "Create convert.py with celsius_to_fahrenheit(c) and "
        "fahrenheit_to_celsius(f), and test_convert.py with unittest tests for both "
        "(including 100°C == 212°F and -40 == -40).",
        "tests": ["test_convert.py"],
    }
    result = agent.run(
        _context(
            agent,
            {
                "mode": "implement",
                "task": task,
                "requirements": {"summary": "Temperature conversion helpers"},
                "architecture": {"style": "single Python module"},
                "technology_selection": {
                    "choices": [{"category": "language", "choice": "Python 3 standard library"}]
                },
                "commands": {"test": [{"argv": ["python", "-m", "unittest", "-v"]}]},
                "file_tree": [],
            },
        )
    )
    changes: CodeChanges = result.output
    if changes.change_request or not changes.all_files:
        return False, "returned no files", result.model
    with tempfile.TemporaryDirectory(prefix="factory-eval-") as tmp:
        root = Path(tmp)
        fs = FilesystemTool(root)
        try:
            for change in changes.all_files:
                if change.action is not FileAction.DELETE:
                    fs.write_text(change.path, change.content)
        except FactoryError as error:
            return False, f"invalid file change: {error}", result.model
        python = Path(sys.executable).name
        run = ShellTool(root, [python], timeout_seconds=60).run(
            [sys.executable, "-m", "unittest", "-v"],
            extra_env={"PYTHONDONTWRITEBYTECODE": "1"},
        )
        output = (run.stdout + "\n" + run.stderr).strip()
        ran = next((line for line in output.splitlines() if line.startswith("Ran ")), "")
        if ran.startswith("Ran 0 ") or not ran:
            written = ", ".join(c.path for c in changes.all_files)
            return False, f"no tests were written (files: {written})", result.model
        if run.success:
            return True, f"generated tests pass ({ran})", result.model
        tail = output.splitlines()[-1] if output else "no output"
        return False, f"generated tests failed (exit {run.exit_code}): {tail}", result.model


CASES: tuple[EvaluationCase, ...] = (
    EvaluationCase("requirement_extraction", "reasoning", _requirement_extraction),
    EvaluationCase("ambiguity_detection", "reasoning", _ambiguity_detection),
    EvaluationCase("architecture_reasoning", "reasoning", _architecture_reasoning),
    EvaluationCase("technology_fit", "reasoning", _technology_fit),
    EvaluationCase("code_generation", "coding", _code_generation),
)


def run_evaluation(
    config: FactoryConfig,
    llm: LLMClient,
    only: list[str] | None = None,
    on_result: Callable[[CaseResult], None] | None = None,
) -> EvaluationReport:
    prompts = PromptLibrary(config.prompts_path)
    report = EvaluationReport(started_at=utc_now())
    selected = [c for c in CASES if not only or c.name in only]
    unknown = sorted(set(only or []) - {c.name for c in CASES})
    if unknown:
        raise FactoryError(
            f"Unknown evaluation case(s): {', '.join(unknown)}",
            hint=f"Cases: {', '.join(c.name for c in CASES)}",
        )
    for case in selected:
        started = time.monotonic()
        try:
            passed, detail, model = case.run(llm, prompts)
        except FactoryError as error:  # model failure or invalid output counts as a fail
            passed, detail, model = False, f"error: {error}", ""
        result = CaseResult(
            case=case.name,
            capability=case.capability,
            passed=passed,
            duration_s=round(time.monotonic() - started, 1),
            detail=detail,
            model=model,
        )
        report.results.append(result)
        if on_result:
            on_result(result)
    _save(config, report)
    return report


def _save(config: FactoryConfig, report: EvaluationReport) -> Path:
    directory = config.workspace_path / "evaluations"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = report.started_at.replace(":", "").replace("+0000", "Z")
    path = directory / f"evaluation-{stamp}.json"
    path.write_text(json.dumps(report.as_dict(), indent=2) + "\n", encoding="utf-8")
    return path
