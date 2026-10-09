# Repository Analysis Tool

**Code:** [`src/software_factory/tools/repository_analysis.py`](../../src/software_factory/tools/repository_analysis.py) · **Node:** `analyze_repository` in [`orchestration/nodes/deterministic_nodes.py`](../../src/software_factory/orchestration/nodes/deterministic_nodes.py) · **Tests:** `tests/unit/test_tools.py` · **Spec:** §68.7, §68.9

## Purpose

Collect **deterministic facts** about an existing repository — languages, frameworks,
package managers, build systems, tests, config files, entry points, git state — from the
file system and git, never from an LLM. Agents may interpret these facts; they do not
produce them.

## How it is used

It is not granted to agents. The deterministic workflow node `analyze_repository` calls
`analyze_repository(Path(repository_path))` (injected via `WorkflowDependencies`):

- Runs after the requirements are approved (lifecycle `REPOSITORY_ANALYSIS`), before `design_architecture`.
- Entry criteria (`repository_analysis` in `domain/quality_gates.py`): requirements approved **and** the requirements artifact exists.
- **Greenfield** projects, or existing projects without a `repository_path`: skipped; `state.repository = None`.
- **Existing** projects: the result is stored in `state.repository`, written as `repository-analysis.json` / `repository-analysis.md` (+ `artifacts/repository-analysis.rev1.json`), and recorded as a `TOOL` execution.
- The result is passed to the Architecture, Technology Selection and Implementation Planning agents as their optional `repository_analysis` input.
- `FactoryError` or `OSError` (e.g. path does not exist) → `FAILED`, retryable with `factory retry`.

## Output schema

`RepositoryAnalysis` ([`domain/repository.py`](../../src/software_factory/domain/repository.py)):

| Field | How it is detected |
|---|---|
| `path` | Resolved root. |
| `languages` | File-suffix map (`.py` Python, `.ts/.tsx` TypeScript, `.js/.jsx/.mjs` JavaScript, Java, Kotlin, Go, Rust, Ruby, PHP, C#, C/C++, Swift, Scala, Dart, SQL, Shell) → file counts, most common first. |
| `dependency_files` / `package_managers` | Known manifests/lockfiles: `pyproject.toml`, `requirements.txt`, `Pipfile`, `poetry.lock`, `uv.lock`, `package.json`, `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `pom.xml`, `build.gradle(.kts)`, `go.mod`, `Cargo.toml`, `Gemfile`, `composer.json`. |
| `build_systems` | From manifests (e.g. Maven, Gradle, Cargo, npm scripts) and `Makefile`, `CMakeLists.txt`, `Dockerfile`, `docker-compose.y(a)ml`. |
| `frameworks` | Substring markers in dependency manifests (Django, Flask, FastAPI, LangGraph, React, Next.js, Vue, Angular, Express, NestJS, Spring Boot, Gin, Actix, Rails, Laravel). For `package.json`, only dependency/devDependency **names** are searched. |
| `test_locations` | Directories named `tests`, `test`, `__tests__`, `spec` (reported as `dir/`), or files named `test_*`, `*.test.*`, `*.spec.*`. Max 50. |
| `configuration_files` | Known names such as `tsconfig.json`, `ruff.toml`, `pytest.ini`, `.eslintrc.*`, `vite.config.*`, `next.config.*`, `.env.example`, `angular.json`. |
| `likely_entry_points` | Known names such as `main.py`, `app.py`, `manage.py`, `index.ts`, `server.js`, `main.go`, `main.rs`, `Program.cs`, `Main.java`. Max 50. |
| `top_level_structure` | Root entries (dirs end with `/`), excluding skip dirs. |
| `total_files_scanned` | Files visited. |
| `git` | `GitTool(root).state()` — see [git tool](git_tool.md). |

## Limits and skipping

- **Skip dirs:** `.git`, `node_modules`, `.venv`, `venv`, `__pycache__`, `dist`, `build`, `.next`, `target`, `.tox`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`, `.idea`, `.vscode`.
- **Symlinks** (files and dirs) are skipped; unreadable directories are skipped silently.
- **Max 20,000 files** scanned (`MAX_FILES`); the walk stops there.
- Only dependency manifests are read (for framework markers); source files are never opened.

## Example (abridged, from the unit test)

```json
{
  "languages": {"Python": 2, "TypeScript": 1},
  "frameworks": ["Next.js", "React"],
  "package_managers": ["npm"],
  "build_systems": ["Docker", "npm scripts"],
  "test_locations": ["api/tests/"],
  "likely_entry_points": ["api/main.py"],
  "git": {"is_repository": false, "changed_files": 0}
}
```

## Known limitations

- Framework detection is substring-based on lowercased manifest text outside `package.json`, so false positives are possible (e.g. `rails` inside another package name).
- Heuristic lists are fixed; unknown ecosystems report nothing rather than guessing.
- Truncation at 20,000 files is not flagged in the output other than via `total_files_scanned`.
- It is a plain function, not a `ToolKit`-provisioned tool (`ToolName.REPOSITORY_ANALYSIS` exists but `ToolKit` cannot build it: asking for it raises `ToolPermissionError` "not available in this build").
