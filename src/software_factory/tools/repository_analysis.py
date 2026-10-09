"""Deterministic repository analysis for existing-project mode.

Raw facts (languages, build files, tests, git state, ...) come from the file
system and git — never from an LLM. Agents may interpret these facts later.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from software_factory.domain.repository import RepositoryAnalysis
from software_factory.tools.git import GitTool

MAX_FILES = 20_000
SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    ".next",
    "target",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".idea",
    ".vscode",
}

LANGUAGE_BY_SUFFIX = {
    ".py": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".java": "Java",
    ".kt": "Kotlin",
    ".go": "Go",
    ".rs": "Rust",
    ".rb": "Ruby",
    ".php": "PHP",
    ".cs": "C#",
    ".cpp": "C++",
    ".cc": "C++",
    ".c": "C",
    ".h": "C/C++ header",
    ".swift": "Swift",
    ".scala": "Scala",
    ".dart": "Dart",
    ".sql": "SQL",
    ".sh": "Shell",
}

DEPENDENCY_FILES = {
    "pyproject.toml": ("pip/uv/poetry", "Python packaging"),
    "requirements.txt": ("pip", None),
    "Pipfile": ("pipenv", None),
    "poetry.lock": ("poetry", None),
    "uv.lock": ("uv", None),
    "package.json": ("npm", "npm scripts"),
    "package-lock.json": ("npm", None),
    "yarn.lock": ("yarn", None),
    "pnpm-lock.yaml": ("pnpm", None),
    "pom.xml": ("maven", "Maven"),
    "build.gradle": ("gradle", "Gradle"),
    "build.gradle.kts": ("gradle", "Gradle"),
    "go.mod": ("go modules", "go build"),
    "Cargo.toml": ("cargo", "Cargo"),
    "Gemfile": ("bundler", None),
    "composer.json": ("composer", None),
}

BUILD_FILES = {
    "Makefile": "Make",
    "CMakeLists.txt": "CMake",
    "Dockerfile": "Docker",
    "docker-compose.yml": "Docker Compose",
    "docker-compose.yaml": "Docker Compose",
}

CONFIG_FILES = {
    ".env.example",
    "tsconfig.json",
    "setup.cfg",
    "tox.ini",
    ".eslintrc.json",
    ".eslintrc.js",
    "eslint.config.js",
    "ruff.toml",
    ".prettierrc",
    "next.config.js",
    "next.config.mjs",
    "vite.config.ts",
    "vite.config.js",
    "jest.config.js",
    "pytest.ini",
    "angular.json",
}

ENTRY_POINT_NAMES = {
    "main.py",
    "app.py",
    "manage.py",
    "__main__.py",
    "server.py",
    "wsgi.py",
    "asgi.py",
    "index.js",
    "index.ts",
    "main.ts",
    "server.js",
    "server.ts",
    "app.js",
    "app.ts",
    "main.go",
    "main.rs",
    "Program.cs",
    "Main.java",
    "Application.java",
}

# (marker in dependency manifest, framework name)
FRAMEWORK_MARKERS = [
    ("django", "Django"),
    ("flask", "Flask"),
    ("fastapi", "FastAPI"),
    ("langgraph", "LangGraph"),
    ('"react"', "React"),
    ('"next"', "Next.js"),
    ('"vue"', "Vue"),
    ('"@angular/core"', "Angular"),
    ('"express"', "Express"),
    ('"@nestjs/core"', "NestJS"),
    ("spring-boot", "Spring Boot"),
    ("gin-gonic", "Gin"),
    ("actix-web", "Actix"),
    ("rails", "Rails"),
    ("laravel", "Laravel"),
]


def analyze_repository(path: Path) -> RepositoryAnalysis:
    root = path.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Repository path does not exist: {root}")

    languages: Counter[str] = Counter()
    dependency_files: list[str] = []
    package_managers: set[str] = set()
    build_systems: set[str] = set()
    test_locations: set[str] = set()
    config_files: list[str] = []
    entry_points: list[str] = []
    scanned = 0

    for file in _walk(root):
        scanned += 1
        relative = file.relative_to(root).as_posix()
        name = file.name
        if file.suffix in LANGUAGE_BY_SUFFIX:
            languages[LANGUAGE_BY_SUFFIX[file.suffix]] += 1
        if name in DEPENDENCY_FILES:
            dependency_files.append(relative)
            manager, build = DEPENDENCY_FILES[name]
            package_managers.add(manager)
            if build:
                build_systems.add(build)
        if name in BUILD_FILES:
            build_systems.add(BUILD_FILES[name])
        if name in CONFIG_FILES:
            config_files.append(relative)
        if name in ENTRY_POINT_NAMES:
            entry_points.append(relative)
        parts = set(file.relative_to(root).parts[:-1])
        if parts & {"tests", "test", "__tests__", "spec"}:
            test_dir = next(
                p
                for p in file.relative_to(root).parts
                if p in {"tests", "test", "__tests__", "spec"}
            )
            test_locations.add(relative.split(test_dir)[0] + test_dir + "/")
        elif name.startswith("test_") or ".test." in name or ".spec." in name:
            test_locations.add(relative)
        if scanned >= MAX_FILES:
            break

    frameworks = _detect_frameworks(root, dependency_files)
    return RepositoryAnalysis(
        path=str(root),
        languages=dict(languages.most_common()),
        frameworks=frameworks,
        package_managers=sorted(package_managers),
        dependency_files=sorted(dependency_files),
        build_systems=sorted(build_systems),
        test_locations=sorted(test_locations)[:50],
        configuration_files=sorted(config_files),
        likely_entry_points=sorted(entry_points)[:50],
        top_level_structure=sorted(
            p.name + ("/" if p.is_dir() else "") for p in root.iterdir() if p.name not in SKIP_DIRS
        ),
        total_files_scanned=scanned,
        git=GitTool(root).state(),
    )


def _walk(root: Path):
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.is_dir():
                if entry.name not in SKIP_DIRS:
                    stack.append(entry)
            else:
                yield entry


def _detect_frameworks(root: Path, dependency_files: list[str]) -> list[str]:
    found: set[str] = set()
    for relative in dependency_files:
        try:
            text = (root / relative).read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            continue
        if relative.endswith("package.json"):
            try:
                data = json.loads(text)
                deps = {**data.get("dependencies", {}), **data.get("devdependencies", {})}
                text = json.dumps(sorted(deps))
            except ValueError:
                pass
        for marker, framework in FRAMEWORK_MARKERS:
            if marker in text:
                found.add(framework)
    return sorted(found)
