# Source Workspace Tool

**Code:** [`src/software_factory/tools/source_workspace.py`](../../src/software_factory/tools/source_workspace.py) · **Used by:** [`development_nodes.py`](../../src/software_factory/orchestration/nodes/development_nodes.py), [`quality_nodes.py`](../../src/software_factory/orchestration/nodes/quality_nodes.py) (file tree for code review, manifest fingerprint for final validation) · **Tests:** `tests/unit/test_build_tools.py` · **Spec:** §41, §68.12

## Purpose

Deterministic helpers for the generated project's source tree:

- which files exist;
- which files are relevant to a task or a failure;
- whether the dependency manifests changed since the last setup run.

Build-phase agents only ever see what these helpers select, within a character budget.
These are plain functions, not a `ToolKit` tool.

## API

```python
list_source_files(root: Path, limit: int = 400) -> list[str]
read_files(root, paths, budget_chars: int, per_file_chars: int = 12_000) -> dict[str, str]
mentioned_files(text: str, known_files) -> list[str]
manifest_fingerprint(root: Path) -> str
DEFAULT_GITIGNORE, DEPENDENCY_MANIFESTS, LOCK_FILES
```

## File listing (`list_source_files`)

- Returns relative POSIX paths, sorted, up to `limit` (400 by default; the fingerprint uses 5,000).
- Skips symlinks and the repository-analysis `SKIP_DIRS`: `.git`, `node_modules`, `.venv`, `venv`, `__pycache__`, `dist`, `build`, `.next`, `target`, `.tox`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`, `.idea`, `.vscode`.
- Unreadable directories are skipped silently.
- Used as `file_tree` for the Coding and Code Review agents.

## Context budget (`read_files`)

- Reads the given paths **in order** (duplicates removed) until `budget_chars` is used up. The build phase passes `limits.max_context_chars` (default 40,000).
- A file longer than `per_file_chars` (12,000) is cut, and `… [truncated]` is appended.
- A file that does not fit the remaining budget is **skipped**, not cut. A smaller file later in the list may still fit.
- **Lock files are never read**: `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `uv.lock`, `poetry.lock`, `Cargo.lock`.
- Paths outside the root, non-files, unreadable and non-UTF-8 files are skipped.

## Relevant-file ordering (`_code_context` in `development_nodes.py`)

The workflow builds the path list as **preferred files → dependency manifests → the rest
of the tree** and passes it to `read_files`. Small projects therefore get their whole
code base, and large ones get the files the task or failure points at. The preferred
files are:

- **Implement mode:** the task's `likely_files`, then its `tests`.
- **Fix mode:** the files named in the failure text (`mentioned_files`), then the files changed last time (`development.last_changes`), then the task's `likely_files`.

`mentioned_files(text, known_files)` returns the known files whose full path appears in
the text, or whose file name (longer than 4 characters) appears in it. For example,
`File "src/app.py", line 1` matches `src/app.py`.

## Manifest fingerprint (`manifest_fingerprint`)

This is the SHA-256 (first 16 hex characters) of the path and content of every file
named in `DEPENDENCY_MANIFESTS`:

- JavaScript: `package.json`, `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`;
- Python: `requirements.txt`, `requirements-dev.txt`, `pyproject.toml`, `uv.lock`, `poetry.lock`;
- other ecosystems: `go.mod`, `Cargo.toml`, `pom.xml`, `build.gradle`, `Gemfile`, `composer.json`.

The `build` and `final_validation` nodes run the plan's `setup` commands only when the
fingerprint differs from `development.setup_fingerprint`. The fingerprint is stored
after a successful setup. See the [build tool](build_tool.md).

## Default `.gitignore` (`DEFAULT_GITIGNORE`)

`prepare_development` writes this to a new greenfield repository when it has no
`.gitignore`, before the initial commit:

```text
# Added by Software Factory
node_modules/
.venv/
venv/
__pycache__/
*.pyc
dist/
build/
.pytest_cache/
.env
*.log
.DS_Store
```

It keeps dependencies, build output and `.env` out of commits, and therefore out of the
secret scan and the review diff.

## Example (from the unit test)

```python
list_source_files(tmp)            # ["big.py", "package-lock.json", "src/app.py"]  (node_modules skipped)
read_files(tmp, ["big.py", "package-lock.json", "src/app.py"], budget_chars=100)
                                  # {"src/app.py": ...}  (5,000-char file skipped, lock file never read)
```

## Known limitations

- The 400-file listing limit means very large repositories are only partly visible to agents.
- Relevance is path-based (plan hints and file names in error output); there is no semantic search.
- `mentioned_files` matching on bare file names can pick up unrelated files that share a common name.
- `manifest_fingerprint` reads manifests without a size limit or error handling.
