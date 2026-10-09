"""Enforce the architectural dependency boundaries of spec §57 by static import scan."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[2] / "src" / "software_factory"


def _imports(module_dir: str) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in (PACKAGE / module_dir).rglob("*.py"):
        names: set[str] = set()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
        found[str(path.relative_to(PACKAGE))] = names
    return found


@pytest.mark.parametrize(
    ("layer", "forbidden"),
    [
        # Agents: LLM interface, tools, domain only. No providers, DB, CLI, HTTP, LangGraph.
        (
            "agents",
            (
                "software_factory.llm.providers",
                "software_factory.llm.router",
                "software_factory.llm.registry",
                "software_factory.persistence",
                "software_factory.cli",
                "software_factory.core",
                "httpx",
                "langgraph",
                "ollama",
                "openai",
            ),
        ),
        # Workflow: agent interface + domain state; persistence arrives via ports.
        (
            "orchestration",
            (
                "software_factory.persistence",
                "software_factory.cli",
                "software_factory.core",
                "software_factory.llm.providers",
                "httpx",
            ),
        ),
        # Domain is pure.
        (
            "domain",
            (
                "software_factory.persistence",
                "software_factory.cli",
                "software_factory.core",
                "software_factory.llm",
                "software_factory.agents",
                "langgraph",
                "httpx",
            ),
        ),
        # The web UI, like the CLI, goes through Factory Core only.
        (
            "web",
            (
                "software_factory.orchestration",
                "software_factory.persistence",
                "software_factory.llm",
                "software_factory.agents",
                "software_factory.tools",
                "software_factory.cli",
                "langgraph",
            ),
        ),
        # Core never talks to a provider directly.
        ("core", ("software_factory.llm.providers", "software_factory.cli", "httpx")),
        # Only the provider package knows about HTTP.
        ("llm/router.py", ("httpx", "software_factory.llm.providers.ollama")),
    ],
)
def test_layer_does_not_import_forbidden_modules(layer, forbidden):
    target = PACKAGE / layer
    modules = _imports(layer) if target.is_dir() else {layer: next(iter(_imports_file(target)))}
    violations = [
        f"{module} imports {name}"
        for module, names in modules.items()
        for name in names
        if any(name == f or name.startswith(f + ".") for f in forbidden)
    ]
    assert not violations, violations


def _imports_file(path: Path) -> list[set[str]]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return [names]


def test_no_model_names_hardcoded_in_source():
    """Model names are configuration (spec §22); none may appear in the package."""
    for path in PACKAGE.rglob("*.py"):
        text = path.read_text().lower()
        for name in ("qwen", "deepseek-", "llama3", "gpt-4", "gemini-", "mistral"):
            assert name not in text, f"{path} mentions model '{name}'"
