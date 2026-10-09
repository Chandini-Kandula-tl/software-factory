"""Canned agent outputs used across tests (test data only)."""

from __future__ import annotations

REQUIREMENT_AGENT = "requirement-agent"
ARCHITECTURE_AGENT = "architecture-agent"
TECHNOLOGY_AGENT = "technology-selection-agent"
PLANNING_AGENT = "implementation-planning-agent"

EMPLOYEE_REQUIREMENTS = (
    "Build an employee management application.\nEmployees should be managed by administrators."
)


def requirements_needing_clarification() -> dict:
    return {
        "summary": "Employee management application administered by admins.",
        "application_type": "unknown",
        "functional_requirements": [
            {"id": "FR-1", "description": "Administrators can create, update and delete employees."}
        ],
        "needs_clarification": True,
        "clarification_questions": [
            {
                "question": "Should this be a web application, mobile application, or both?",
                "reason": "Determines the client architecture",
                "impact_area": "architecture",
                "required": True,
            },
            {
                "question": "Who should be able to access employee information?",
                "reason": "Determines the authorization model",
                "impact_area": "security",
                "required": True,
            },
            {
                "question": "Do you have a preferred color scheme?",
                "required": False,
                "impact_area": "other",
            },
        ],
    }


def clear_requirements() -> dict:
    return {
        "summary": "Web application where administrators manage employee records.",
        "application_type": "web application",
        "functional_requirements": [
            {
                "id": "FR-1",
                "description": "Administrators can create, update and delete employees.",
            },
            {
                "id": "FR-2",
                "description": "Employees can view their own profile.",
                "source": "clarification",
            },
        ],
        "non_functional_requirements": [
            {"id": "NFR-1", "description": "Only authenticated users can access data."}
        ],
        "assumptions": ["Up to 500 employees"],
        "acceptance_criteria": [
            {"id": "AC-1", "requirement_ids": ["FR-1"], "description": "Admin can add an employee."}
        ],
        "needs_clarification": False,
        "clarification_questions": [],
    }


def architecture() -> dict:
    return {
        "style": "modular monolith",
        "overview": "A web client talking to a single backend service with a relational store.",
        "components": [
            {"name": "Web client", "responsibility": "UI", "requirement_ids": ["FR-1", "FR-2"]},
            {
                "name": "Backend API",
                "responsibility": "Business logic and auth",
                "requirement_ids": ["FR-1", "FR-2", "NFR-1"],
            },
        ],
        "risks": ["Single deployment unit"],
        "alternatives": [
            {
                "name": "Microservices",
                "summary": "Split per domain",
                "reason_not_chosen": "Too complex for the scale",
            }
        ],
    }


def technology() -> dict:
    return {
        "summary": "Lightweight web stack.",
        "choices": [
            {
                "category": "backend",
                "choice": "FastAPI",
                "rationale": "Simple typed APIs",
                "requirement_ids": ["FR-1"],
            },
            {"category": "database", "choice": "SQLite", "rationale": "Small data volume"},
        ],
        "not_applicable": ["messaging: no async workloads"],
    }


def plan() -> dict:
    return {
        "summary": "Two milestones.",
        "release_artifact": "Source repository with a runnable web service",
        "commands": {"test": [{"argv": ["python", "-c", "print('ok')"]}]},
        "milestones": [{"name": "M1", "task_ids": ["T-1", "T-2"]}],
        "tasks": [
            {
                "id": "T-1",
                "title": "Scaffold",
                "description": "Create project skeleton",
                "requirement_ids": ["FR-1"],
            },
            {
                "id": "T-2",
                "title": "Employee CRUD",
                "description": "Implement CRUD",
                "depends_on": ["T-1"],
                "requirement_ids": ["FR-1"],
                "tests": ["test_crud"],
            },
        ],
    }


# -- build phase: a tiny real Python project ------------------------------------------

CODING_AGENT = "coding-agent"
TESTING_AGENT = "testing-agent"
REVIEW_AGENT = "code-review-agent"

UNITTEST = {"argv": ["python", "-m", "unittest", "discover", "-s", "tests", "-v"]}


def calculator_plan() -> dict:
    return {
        "summary": "Calculator library.",
        "release_artifact": "Source repository",
        "commands": {"test": [UNITTEST]},
        "tasks": [
            {
                "id": "T-1",
                "title": "Add function",
                "description": "add(a, b)",
                "requirement_ids": ["FR-1"],
                "acceptance_criteria_ids": ["AC-1"],
                "tests": ["tests/test_calc.py"],
            },
            {
                "id": "T-2",
                "title": "Subtract function",
                "description": "sub(a, b)",
                "depends_on": ["T-1"],
                "requirement_ids": ["FR-2"],
                "tests": ["tests/test_calc.py"],
            },
        ],
    }


CALC_V1 = "def add(a, b):\n    return a + b\n"
CALC_V1_BUGGY = "def add(a, b):\n    return a - b\n"
CALC_V2 = CALC_V1 + "\n\ndef sub(a, b):\n    return a - b\n"
TEST_V1 = (
    "import unittest\nfrom calc import add\n\n\nclass T(unittest.TestCase):\n"
    "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n"
)
TEST_V2 = (
    "import unittest\nfrom calc import add, sub\n\n\nclass T(unittest.TestCase):\n"
    "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n\n"
    "    def test_sub(self):\n        self.assertEqual(sub(5, 3), 2)\n"
)


def _path(name: str) -> str:
    """Keyword-friendly file names: tests__test_calc_py -> tests/test_calc.py."""
    stem, _, ext = name.rpartition("_")
    return f"{stem.replace('__', '/')}.{ext}"


def code(summary: str, **files: str) -> dict:
    return {
        "summary": summary,
        "files": [
            {"path": _path(name), "action": "create", "content": content}
            for name, content in files.items()
        ],
    }


def task1(buggy: bool = False) -> dict:
    return code("add", calc_py=CALC_V1_BUGGY if buggy else CALC_V1, tests__test_calc_py=TEST_V1)


def task2() -> dict:
    return code("sub", calc_py=CALC_V2, tests__test_calc_py=TEST_V2)


def analysis(category: str = "code_bug") -> dict:
    return {
        "summary": "add is wrong",
        "failure_category": category,
        "root_cause": "add subtracts",
        "suggested_fix": "return a + b",
        "files_to_change": ["calc.py"],
    }


def clean_review() -> dict:
    return {
        "summary": "Looks good.",
        "findings": [],
        "requirement_coverage": [
            {"requirement_id": "FR-1", "status": "implemented"},
            {"requirement_id": "FR-2", "status": "implemented"},
            {"requirement_id": "NFR-1", "status": "implemented"},
        ],
    }


def blocking_review() -> dict:
    review = clean_review()
    review["findings"] = [
        {
            "id": "R-1",
            "severity": "high",
            "category": "correctness",
            "file": "calc.py",
            "description": "No docstrings on public API",
            "suggestion": "Add docstrings",
        }
    ]
    return review


IMPACT_AGENT = "impact-analysis-agent"


def impact() -> dict:
    return {
        "summary": "Touches the notes module and its tests.",
        "affected_requirements": ["FR-1"],
        "affected_components": ["CLI"],
        "affected_files": ["src/main.py"],
        "regression_areas": ["Existing note listing"],
        "risks": ["Changing storage format"],
        "recommendation": "Proceed; keep the file format backwards compatible.",
    }
