# Security Architecture

The factory writes and executes generated code and runs project commands.
This document maps spec §44 requirements to what the code does today, and
states plainly what is **not** protected.

Tool details: [filesystem](../tools/filesystem_tool.md) ·
[git](../tools/git_tool.md) · [shell](../tools/shell_tool.md) ·
[build](../tools/build_tool.md) · [test](../tools/test_tool.md) ·
[repository analysis](../tools/repository_analysis_tool.md).

## Current exposure

- **Design phase:** no agent is given any tool. The only tool run is the
  read-only repository analysis (existing mode), which uses `GitTool` for
  `rev-parse` and `status`.
- **Build phase:** agents still call no tools themselves. The workflow
  (`orchestration/nodes/development_nodes.py`, and
  `orchestration/nodes/quality_nodes.py` for review, security, final
  validation and release) applies the Coding Agent's proposed file changes
  with `FilesystemTool`, runs the **approved plan's commands** through the
  step's project-command tool (`BuildTool`, `TestTool`, … on top of
  `ShellTool`), and commits/tags with `GitTool`.
- **No sandbox.** Generated code and its build/test commands run **on the
  host**, as your user, with the repository (greenfield:
  `projects/<id>/source/`; existing: your repository) as working directory.
  A test suite, a `setup` step (`npm install`, `pip install`) or package
  install scripts can do anything your user can. Review the plan's commands
  and the approved technology before approving the plan.
- **Least privilege is enforced** by `tools/toolkit.ToolKit`: when the
  workflow acts on an agent's behalf it asks for the tool with
  `ToolKit.require(name, agent.spec.allowed_tools)`, which raises
  `ToolPermissionError` ("Tool '<name>' is not allowed here") if the agent
  did not declare it. The Coding Agent's files are written only this way
  (it declares `filesystem`); every other agent declares no tools. Project
  commands get their shell from `ToolKit.shell()`, built from `security`
  config.

## Build-phase command execution

```text
approved plan.commands  (setup, format, lint, build, test, package, audit; argv lists)
  ▼ create_implementation_plan: command_problems() → state.plan_checks, shown in
  │                      implementation-plan.md ("Environment check") and at the
  │                      CLI approval prompt, before the human approves
  ▼ prepare_development: same command_problems(): shell_enabled; every cwd inside
  │                      the repo; argv[0] not forbidden, in security.allowed_commands
  │                      AND installed, else FAILED before any code is written;
  │                      records `<program> --version` (development.tool_versions)
  ▼ tool_for_step(step): PackageManagerTool / FormatterTool / LintTool / BuildTool /
  │                      TestTool / DependencyAuditTool (history: details.tool)
  ▼ ShellTool(repository root, security.allowed_commands − always-forbidden)
  │   argv only, no shell; cwd must stay inside the repository
  │   env = scrubbed host env + CI=true NO_COLOR=1 FORCE_COLOR=0 PYTHONDONTWRITEBYTECODE=1
  │         − factory venv bin on PATH, − VIRTUAL_ENV, − PYTHONHOME
  │         (security.isolate_project_environment, default true)
  │   timeout = security.command_timeout_seconds
  │   output redacted, capped at 20 000 chars per stream
  ▼ full log → artifacts/tool-runs/NNN-<step>.log (redacted)
  ▼ pass/fail = exit_code == 0 and not timed_out
    (a command refused by policy at run time is recorded as a failed run)
```

- Only commands from the approved plan run; agents cannot add commands.
  Changing them needs a new plan approval (reject the plan, or an approved
  change request).
- Always-forbidden executables, removed from the allowlist even if listed:
  `sh`, `bash`, `zsh`, `sudo`, `su`, `rm`, `curl`, `wget`.
- Default allowlist (`config/factory.yaml` and the `SecurityConfig` code
  default): `git, python, python3, pip, pytest, ruff, uv, node, npm, npx`.
- The build machine's `environment` (allowed / installed commands) is given
  to the Technology Selection and Implementation Planning agents so plans
  use runnable programs.
- **Isolation from the factory's own environment**
  (`security.isolate_project_environment`, default `true`): project commands
  run with the factory's virtual-environment `bin` directory removed from
  `PATH` and `VIRTUAL_ENV` / `PYTHONHOME` unset (`project_environment()` in
  `tools/shell.py`). The installed-commands facts and the plan's "not
  installed" check use the same lookup (`find_program()`). Found live: the
  factory's venv made `python` and `pytest` look installed, so a generated
  app depended on the factory's tools without anyone noticing. This is
  environment hygiene, not a sandbox.
- A greenfield project's `source/` is always its own git repository (checked
  with `git rev-parse --show-toplevel`), so factory commits and tags can never
  land in an enclosing repository such as the factory's own checkout.
- `security.shell_enabled: false` disables all command execution, including
  git, so the build phase cannot run.

## File writes by the Coding Agent

- Output validation (`agents/coding.py`) checks every entry of
  `CodeChanges.all_files` (`files` + `test_code`): it rejects absolute
  paths, `..`, empty paths and anything under `.git/`; the same checks apply
  to `edits` paths, and an edit's `find` must be non-empty. The model gets
  one correction attempt with the exact reason
  (`LLMAgent.validation_repairs`); persistent violations → `FAILED`. A path
  given twice is not a violation: the last copy is kept, with a warning.
- A targeted edit is applied only if its `find` text occurs exactly once in
  the existing file (`ChangeApplyError` otherwise). In a multi-candidate fix
  round, every candidate's touched files are snapshotted and a failing
  candidate is restored, so rejected fixes leave no trace in the repository.
- `_apply_changes` obtains `FilesystemTool` only through
  `ToolKit.require(FILESYSTEM, coding agent's allowed_tools)`.
  `FilesystemTool` (root = repository) re-checks every path after resolving
  symlinks, refuses to delete directories and caps file size (2 MB).
- The agent sees only files selected by `tools/source_workspace.py` within
  `limits.max_context_chars`; lock files are never sent.

## Security validation stage

Deterministic, no LLM (`security_validation` node, `tools/secret_scan.py`):

| Check | Severity |
|---|---|
| Tracked file named `.env`, `id_rsa`, `id_ed25519`, `credentials.json`, or ending `.pem`, `.key`, `.p12`, `.pfx` | critical |
| Private key block, `sk-…`, AWS `AKIA…`, GitHub `gh[pousr]_…`, Google `AIza…`, Slack `xox…` token in a tracked file | critical |
| Hard-coded credential literal (`*password*`, `*secret*`, `*api_key*`, `*token*` … `= "≥6 chars"`) | high; medium in test code (`tests/`, `test_*`, `*.test.*`, `*.spec.*`, fixtures) |
| Plan `audit` command fails | medium |
| Code review finding with category `security` | as reviewed; listed, never blocks here |

Placeholder values (`example`, `changeme`, `your_…`, `xxx`, `dummy`,
`<…>`) are ignored; values read from the environment are fine. Files over
1 MB or not UTF-8 are skipped.

**Blocking rule:** any `critical`/`high` finding that is not a review
finding blocks. The Coding Agent gets up to `limits.max_review_rounds` fix
rounds; if findings remain, the project goes to `FAILED` (remove the secret
by hand, then `factory retry`; once all tasks are done the retry commits
your manual edits as `chore: manual changes before retry` and restarts the
fix rounds). `release` additionally requires `security.passed`. Each run
also appends a `WORKFLOW` history record (`security_validation`, success or
failed, with the finding count).

The default greenfield `.gitignore` excludes `.env`, dependency folders,
build output and logs.

## Requirement → implementation

| Spec §44 requirement | Implementation |
|---|---|
| Tool allowlists | `ShellTool` runs only executables in `security.allowed_commands` minus the always-forbidden set; `command_problems` checks plan programs on the plan (before approval) and again in `prepare_development` |
| Least privilege | Agents call no tools; the workflow applies changes and runs only approved commands. The Coding Agent's writes go through `ToolKit.require(FILESYSTEM, allowed_tools)`, so a tool the agent did not declare is refused |
| Command validation | argv lists only (`shell=False`); empty commands rejected; working directory must be inside the root |
| Workspace isolation | Commands and writes are confined to the repository root. The *process* itself is not confined (no sandbox) |
| Path traversal protection | Coding Agent output validation + `FilesystemTool.resolve` (symlinks followed) → `PathOutsideWorkspaceError`. Project ids validated by regex |
| Secret redaction | `observability/redaction.redact` on every artifact write, tool log, log message and persisted error |
| Secret detection in generated code | Security validation stage (above) |
| Git safety | `GitTool` allows only known subcommands; destructive ones need an approval reference (below). The factory never pushes |
| Release integrity | Approving the release is refused ("The code changed after final validation, so it cannot be released as is") unless HEAD equals `validation.head_commit` and the tree is clean (`FactoryService._require_validated_code`) |
| External changes | `factory continue` compares the repository's HEAD with `development.last_commit` and counts uncommitted files; differences are reported and recorded in history (stage `resume`), not blocked, and go through the next build/test/review/security checks |
| Execution timeouts | `security.command_timeout_seconds` (default 600 s); `GitTool` 60 s. Timeout = failure |
| Process limits | Output capped at 20 000 characters per stream. CPU/memory limits: **Planned — not implemented yet** |
| No arbitrary deployment | No deployment code exists; release = a local git tag |
| No unrestricted network | **Not enforced.** `security.network_enabled` (default `false`) is not read by any code. Setup commands such as `npm install` / `pip install` need the network and get it; so does any generated code under test |
| Sandbox / container isolation | **Planned — not implemented yet** (no Docker) |

## Secrets

- API keys are never in config: a provider names an environment variable
  (`api_key_env`). `.env` / `.env.*` are gitignored (`.env.example`
  excepted); `load_config()` loads `./.env` before reading the YAML.
- `observability/redaction.SECRET_ENV_HINTS` = `KEY`, `TOKEN`, `SECRET`,
  `PASSWORD`, `CREDENTIAL` drives both redaction and environment scrubbing:
  commands run by `ShellTool` / `GitTool` (including generated tests) do not
  see variables whose names contain these fragments.
- `redact()` removes secret-looking env values (8+ chars), `sk-…`, `AKIA…`,
  `gh[pousr]_…`, `AIza…`, `Bearer …`, `key=`/`secret:`/`password=`/`token=`
  assignments and PEM private key blocks.
- Logs (`logs/factory.log`, JSON lines) are redacted.

## Git policy (`tools/git.py`)

| Class | Subcommands / flags | Behaviour |
|---|---|---|
| Safe | `status, diff, log, rev-parse, branch, checkout, switch, add, commit, init, show, ls-files, tag` | Allowed |
| Dangerous subcommands | `push, reset, clean, rebase, filter-branch, gc, prune, reflog, rm, stash` | Refused unless `approval_reference` is passed |
| Dangerous flags (any subcommand) | `--force, -f, -D, --hard, --delete, -d` | Refused unless `approval_reference` is passed |
| Discarding checkout | `git checkout -- <path>`, `git checkout .` | Refused unless `approval_reference` is passed |
| Anything else | e.g. `config`, `remote`, `fetch` | Always refused |

The build phase uses `init`, `checkout`/`checkout -b` (existing mode:
branch `factory/<project-id>`), `add -A`, `commit --allow-empty`,
`rev-parse`, `diff`, `ls-files`, `tag -a` — never a dangerous operation.
Commits and tags run with `commit.gpgsign=false`/`tag.gpgsign=false`; if no
git identity is configured (`git config --get user.email`, checked in the
target repository), `Software Factory <software-factory@localhost>` is used. Existing repositories must be clean before the factory touches
them, and the originally checked-out branch is not modified.

Git runs through `ShellTool` (allowlist `["git"]`, root = repository), so it
inherits argv-only execution, env scrubbing, timeouts and redaction. The
approval reference is any non-empty string; linking it to a recorded human
decision is **Planned — not implemented yet**.

## Ground truth

`CommandRun.success` / `ToolResult.success` is
`exit_code == 0 and not timed_out` — derived from the real process, never
from agent text (spec §40, §68.12). The Testing Agent only explains failures;
the Code Review Agent's findings drive fix rounds but cannot mark tests as
passed.

## Workflow-level controls

- The LLM cannot change lifecycle status; transitions are validated against
  `ALLOWED_TRANSITIONS`.
- Every human decision is persisted with who/when/why; rejections require a
  reason. Nothing is released without the release approval gate.
- All loops are bounded: `max_agent_retries`,
  `max_structured_output_repairs`, `max_clarification_rounds`,
  `max_debug_iterations`, `fix_candidates`, `max_review_rounds`, and stuck
  detection stops after the same failure 3 times in a row
  (`NO_PROGRESS_LIMIT`). There is no infinite autonomous loop.
- A human can steer a failed build with `factory retry --hint "…"`; the hint
  is recorded in history and passed to the agents as data in the prompt.
- Release integrity: final validation records files the test run itself
  wrote (`validation.test_side_effects`); the release guard refuses any other
  difference from the validated commit.
- A Testing Agent `environment` diagnosis stops for a human instead of
  letting the Coding Agent "fix" the environment.
- One process per project at a time (`fcntl` run lock).

## Web UI (`factory ui`)

| Risk | Control | Where |
|---|---|---|
| Other machines reaching the UI | The server binds to `127.0.0.1` only | `cli/main.py` (`ui`) |
| Another website triggering actions (CSRF) | Every POST form carries a random token generated at server start; a missing or wrong token → HTTP 403 | `web/app.py` (`check`) |
| Model-written content running scripts (XSS) | Markdown is rendered with `markdown-it-py` in CommonMark mode with `html=False`; Jinja2 auto-escapes everything else | `web/app.py` (`render_markdown`) |
| Reading arbitrary files | Documents are served only from a fixed whitelist of project Markdown names (`DOCUMENTS`); the download zips only the project's generated `source` folder, skipping `.git` and dependency folders | `core/factory_service.py` |
| Two actions racing on one project | One background action per project in the UI (`JobRunner`), plus the factory's cross-process run lock | `web/jobs.py`, `core/workflow_service.py` |

There is no login: the UI is meant for the person at this computer. Serving it
to other people would need authentication and TLS (planned, not implemented).

## Planned — not implemented yet

- Sandboxed / containerized execution of generated code and project
  commands.
- Enforcing `security.network_enabled` (and allowing network only for
  `setup`).
- CPU/memory/process-count limits.
- Binding dangerous Git operations to a recorded approval.
- Dependency vulnerability scanning beyond an optional plan `audit`
  command.
