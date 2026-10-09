"""Model evaluation cases: deterministic checks on real agent outputs."""

from __future__ import annotations

import json

from software_factory.core.factory_service import FactoryService
from software_factory.core.model_evaluation import run_evaluation
from software_factory.domain.errors import FactoryError
from tests.support import fixtures as fx

GOOD_REQUIREMENTS = {
    "summary": "Notes CLI",
    "functional_requirements": [
        {"id": "FR-1", "description": "Add a note"},
        {"id": "FR-2", "description": "List notes"},
        {"id": "FR-3", "description": "Delete a note"},
    ],
    "non_functional_requirements": [
        {"id": "NFR-1", "description": "Listing takes under one second"}
    ],
}
VAGUE = {
    "summary": "Unclear",
    "clarification_questions": [{"question": "What things?", "required": True}],
    "needs_clarification": True,
}
ARCHITECTURE = {
    "style": "cli",
    "overview": "x",
    "components": [
        {"name": "CLI", "responsibility": "io", "requirement_ids": ["FR-1", "FR-2"]},
        {"name": "Store", "responsibility": "file", "requirement_ids": ["FR-3"]},
    ],
    "alternatives": [{"name": "SQLite", "summary": "db"}],
}
PYTHON_TECH = {
    "summary": "Python",
    "choices": [{"category": "language", "choice": "Python 3", "rationale": "installed"}],
}
NODE_TECH = {
    "summary": "Node",
    "choices": [{"category": "runtime", "choice": "Node.js", "rationale": "popular"}],
}
CONVERT = "def celsius_to_fahrenheit(c):\n    return c * 9 / 5 + 32\n"
TEST_CONVERT = (
    "import unittest\nfrom convert import celsius_to_fahrenheit\n\n\n"
    "class T(unittest.TestCase):\n"
    "    def test_boiling(self):\n        self.assertEqual(celsius_to_fahrenheit(100), 212)\n"
)


def test_all_cases_pass_with_good_answers(factory: FactoryService, script, tmp_path):
    script.add(fx.REQUIREMENT_AGENT, GOOD_REQUIREMENTS, VAGUE)
    script.add(fx.ARCHITECTURE_AGENT, ARCHITECTURE)
    script.add(fx.TECHNOLOGY_AGENT, PYTHON_TECH)
    script.add(
        fx.CODING_AGENT, fx.code("convert", convert_py=CONVERT, test_convert_py=TEST_CONVERT)
    )
    seen = []
    report = run_evaluation(factory.config, factory.llm, on_result=seen.append)
    assert [r.case for r in seen] == [
        "requirement_extraction",
        "ambiguity_detection",
        "architecture_reasoning",
        "technology_fit",
        "code_generation",
    ]
    assert report.passed == 5, [r.detail for r in report.results]
    assert "generated tests pass (Ran 1 test" in report.results[-1].detail
    saved = list((factory.config.workspace_path / "evaluations").glob("*.json"))
    assert json.loads(saved[0].read_text())["passed"] == 5


def test_weak_answers_fail_with_reasons(factory: FactoryService, script):
    weak = dict(GOOD_REQUIREMENTS, non_functional_requirements=[])
    script.add(fx.REQUIREMENT_AGENT, weak, dict(GOOD_REQUIREMENTS))  # vague request not questioned
    script.add(fx.ARCHITECTURE_AGENT, dict(ARCHITECTURE, alternatives=[]))
    script.add(fx.TECHNOLOGY_AGENT, NODE_TECH)
    broken = fx.code(
        "convert",
        convert_py="def celsius_to_fahrenheit(c):\n    return c\n",
        test_convert_py=TEST_CONVERT,
    )
    script.add(fx.CODING_AGENT, broken)
    report = run_evaluation(factory.config, factory.llm)
    assert report.passed == 0
    details = {r.case: r.detail for r in report.results}
    assert "one-second" in details["requirement_extraction"]
    assert "did not ask" in details["ambiguity_detection"]
    assert "no alternative" in details["architecture_reasoning"]
    assert "node" in details["technology_fit"]
    assert "generated tests failed" in details["code_generation"]


def test_code_without_tests_is_reported_plainly(factory: FactoryService, script):
    script.add(fx.CODING_AGENT, fx.code("convert", convert_py=CONVERT))
    report = run_evaluation(factory.config, factory.llm, only=["code_generation"])
    assert report.results[0].detail == "no tests were written (files: convert.py)"


def test_model_errors_count_as_failures_and_unknown_cases_are_rejected(factory, script):
    report = run_evaluation(factory.config, factory.llm, only=["ambiguity_detection"])
    assert not report.results[0].passed and report.results[0].detail.startswith("error:")
    try:
        run_evaluation(factory.config, factory.llm, only=["benchmarks"])
    except FactoryError as error:
        assert "Unknown evaluation case" in str(error)
    else:
        raise AssertionError("expected FactoryError")
