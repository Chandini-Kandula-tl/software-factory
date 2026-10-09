# Persistence Architecture

Where project data lives, who writes it, and how it survives restarts.
Machine state is SQLite + JSON; human-readable knowledge is Markdown
(spec §2.8, §30–§31).

## Workspace layout

The workspace path comes from `factory.workspace` in the config, resolved
relative to the config file's directory (default config:
`config/factory.yaml` → `../factory-workspace`). The whole workspace is
runtime data and is gitignored (`factory-workspace/`, `*.sqlite`, `*.db`).

```text
factory-workspace/
├── factory.db                       project registry (SQLite)
├── logs/
│   └── factory.log                  JSON-lines log, redacted
├── evaluations/
│   └── evaluation-<timestamp>.json  `factory evaluate` report (cases, pass/fail, models)
└── projects/
    └── <project-id>/                one isolated directory per project
        ├── project.md               name, id, mode, repository, versions, status (+ "paused")
        ├── requirements-input.md    raw requirements given at create / `factory requirements`
        ├── clarification-questions.md   pending questions (or "None pending")
        ├── clarification-answers.json   answers saved by `factory answer` (only once answered)
        ├── requirements.md / .json
        ├── repository-analysis.md / .json   existing-project mode only
        ├── impact-analysis.md / .json   existing-project mode only: Impact Analysis Agent
        │                                result (latest only, no revision copy)
        ├── architecture.md / .json
        ├── technology-stack.md / .json
        ├── implementation-plan.md / .json   tasks + project commands + environment check
        ├── source/                  generated git repository (greenfield only; existing
        │                            projects are built in their own repository)
        ├── test-results.md / .json  latest failed build run or `test` step run (commands,
        │                            exit codes, output tail)
        ├── test-analysis.md / .json latest Testing Agent failure analysis
        ├── change-request.md / .json    latest change request from the Coding Agent; the .md
        │                                has Problem, Proposed change, "Impact (Coding Agent's
        │                                assessment)", "## Impact analysis" (Impact Analysis
        │                                Agent result or "Impact analysis failed; see errors.md")
        │                                and the deterministic "What is affected so far"
        │                                (tasks, changed files)
        ├── review.md / .json        latest code review (revisioned)
        ├── security.md / .json      latest security validation report
        ├── validation.md / .json    latest final validation incl. traceability table
        ├── release-notes.md / .json tag, commit, release artifact, repository (.md also lists
        │                            requirements, implemented tasks with commits, decisions)
        ├── decisions.md             human approval/rejection log
        ├── progress.md              current stage, pending action, stage checklist,
        │                            task progress, open issues
        ├── errors.md                every recorded failure with hint
        ├── state.json               full current FactoryState
        ├── execution-history.json   append-only execution records
        ├── artifacts/
        │   ├── <stem>.rev<N>.json   every revision of design artifacts and reviews
        │   └── tool-runs/
        │       └── NNN-<step>.log   full output of every project command
        │                            (setup, format, lint, build, test, package, audit)
        └── checkpoints/
            ├── workflow-checkpoints.sqlite   LangGraph checkpoints (thread_id = project id)
            └── run.lock                      fcntl lock: one process advances a project
```

Spec §45 workspace folders `templates/`, `prompts/`, `config/`, `runtime/`
are not used: prompts and config live in the repository (`prompts/`,
`config/`).

## Components

| Component | File | Stores |
|---|---|---|
| `ProjectRegistry` | `persistence/database.py` | `factory.db` table `projects`: `project_id` (PK), `name`, `description`, `mode`, `repository_path`, `created_at`, `factory_version`, `paused`, `lifecycle_status` |
| Checkpointer | `persistence/checkpoints.py` | `open_checkpointer(project_dir)` → LangGraph `SqliteSaver` on the project's own file; `thread_config(id)` = `{"configurable": {"thread_id": id}}` |
| `ArtifactStore` | `persistence/artifacts.py` | All files in the project directory |
| Renderers | `persistence/markdown.py` | `render_project`, `render_requirements`, `render_architecture`, `render_technology`, `render_plan`, `render_repository_analysis`, `render_progress`, `render_decisions`, `render_errors`; build phase: `render_verification`, `render_test_analysis`, `render_review`, `render_security`, `render_validation`, `render_change_request`, `render_release_notes` |
| `ProjectRecorder` | `core/project_recorder.py` | Implements the workflow's `WorkflowRecorder` port using the store + registry |

The registry holds only the list of projects and headline status. Workflow
state lives exclusively in each project's own checkpoint database, so
projects never share workflow storage.

## Write guarantees

- **Atomic:** `write_text` writes to a temp file in the same directory, then
  `os.replace`. An interrupted process never leaves a half-written file.
- **Redacted:** every `write_text` (and so every `write_json`) passes the
  content through `observability.redaction.redact` — secrets from provider
  errors or tool output cannot reach project files
  (`tests/workflow/test_design_workflow.py::test_secrets_are_not_persisted`).
- **Revisioned:** `write_stage_artifact(stem, model, markdown, revision)`
  writes `<stem>.json`, `<stem>.md` and `artifacts/<stem>.rev<N>.json`.
  `N` comes from `artifact_revisions[stage]`, incremented on each agent run
  (rejection → new revision). Repository analysis is always `rev1`.
- **Latest only:** build-phase reports are rewritten in place by
  `write_artifact(stem, model, markdown)` — `<stem>.json` + `<stem>.md`, no
  revision copy (see stems below). The code review is the exception: it is
  revisioned like design artifacts.
- **Tool logs:** `ProjectRecorder.write_tool_output(name, content)` writes
  `artifacts/tool-runs/<NNN>-<name>` (numbered by the count of existing
  files, never overwritten) and returns the relative path, which is stored in
  the `CommandRun`, the execution record and `test-results.md`. Each log
  starts with the command, `cwd` and exit code. Like every write, it is
  redacted.

## When files are written

| Trigger | Files |
|---|---|
| `factory create` | `project.md`, `artifacts/`, `checkpoints/`, registry row; `requirements-input.md` if requirements were given |
| `factory requirements` (before start only) | `requirements-input.md` |
| After **every** node (`_persisting` → `ProjectRecorder.persist_state`) | `state.json`, `progress.md`, `decisions.md`, `errors.md`, `project.md`, `clarification-questions.md`; registry `lifecycle_status` updated |
| Design agent node success | stage `<stem>.md/.json` + revision copy |
| `prepare_development` (greenfield) | `source/` with `git init`, `.gitignore`, initial commit |
| `create_implementation_plan` | `implementation-plan.md` also shows "Commands the factory will run" (step, command, directory) and an "Environment check" section (⚠️ with the `plan_checks` problems, or "Every command's program is allowed and installed.") |
| `implement_task`, `debug` | files in the repository (via `FilesystemTool`, obtained with `ToolKit.require`); `change-request.md/.json` if one is raised (after the Impact Analysis Agent ran in mode `change_request`; its result is stored in `state.change_request.impact_analysis`, a failure is added to `errors.md`); `test-analysis.md/.json` (debug, command failures) |
| `build`, `test`, `final_validation`, `security_validation` (audit) | one `artifacts/tool-runs/NNN-<step>.log` per command |
| `build` (failed) | `test-results.md/.json` |
| `test` | `test-results.md/.json` (also when the planned-test-files check fails); a git commit when green |
| `code_review` | `review.md/.json` + `artifacts/review.rev<N>.json` |
| `security_validation` | `security.md/.json`; a `workflow` history record |
| `final_validation` | `validation.md/.json` (incl. `head_commit`); a `workflow` history record |
| `release` | git tag `release-N`; `release-notes.md/.json` |
| `analyze_repository` (existing mode) | `repository-analysis.md/.json` + `artifacts/repository-analysis.rev1.json`; then `impact-analysis.md/.json` (Impact Analysis Agent, `state.impact_analysis`) |
| Every node that does work or takes a decision | one record appended to `execution-history.json` and one log line |
| `factory answer` | `clarification-answers.json` (immediately, before `continue`) |
| `factory continue` after clarification | answered ids removed from `clarification-answers.json` once the graph has consumed them |
| `factory continue` with external repository changes | one `workflow` record (stage `resume`, status `info`, `details.git`) in `execution-history.json` |
| LangGraph, after each completed node | a checkpoint in `workflow-checkpoints.sqlite` |

Stems (`core/project_recorder.py`):

| Map | Stage key → file stem |
|---|---|
| `ARTIFACT_STEMS` (revisioned) | `requirements` → `requirements`, `architecture` → `architecture`, `technology` → `technology-stack`, `implementation_plan` → `implementation-plan`, `repository_analysis` → `repository-analysis`, `code_review` → `review` |
| `LATEST_ONLY_STEMS` | `test_results` → `test-results`, `test_analysis` → `test-analysis`, `security` → `security`, `validation` → `validation`, `change_request` → `change-request`, `impact_analysis` → `impact-analysis`, `release` → `release-notes` |

`progress.md` lists the stages (requirements … implementation plan,
development, code review, security validation, final validation, release)
with checkboxes from `completed_stages` and, once development started, a
line `Tasks: <done>/<total> done · current: <task> · fix attempts: <n> ·
code in <source_path>`. `PENDING_ACTIONS` gives the next human action per
status (e.g. at `WAITING_FOR_RELEASE_APPROVAL`: review `validation.md`,
`review.md` and `security.md`, then approve or reject).

## Execution history

`execution-history.json` is a JSON array of `ExecutionRecord`
(`domain/execution.py`):

| Field | Meaning |
|---|---|
| `execution_id` | `exec-<12 hex>` |
| `project_id`, `stage`, `kind` | kind: `agent`, `tool`, `human_decision`, `clarification`, `workflow` (`error` exists but is unused). Build-phase tool records use the command step as `stage` and carry `details.tool` (e.g. `build`, `test`, `package_manager`, `dependency_audit`), `details.cwd` and `details.log_file`. The `prepare_development` workflow record carries `details.task_queue` and `details.tool_versions` |
| `status` | `success`, `failed`, `info` |
| `started_at`, `completed_at`, `duration_ms` | UTC ISO timestamps, wall time |
| `agent`, `agent_version`, `model_profile`, `provider`, `model` | who ran |
| `prompt_versions` | `{"requirement/system.md": "1.0.0+<hash>", …}` |
| `llm_calls[]` | per attempt: `provider`, `model`, `model_profile`, `status` (`success` / `failed` / `invalid_output`), `attempt`, `latency_ms`, `input_tokens`, `output_tokens`, `error_kind`, `error` |
| `retry_count` | `len(llm_calls) − 1` (counts retries, repairs and fallbacks) |
| `factory_version`, `workflow_version` | e.g. `0.2.0`, `full-lifecycle-1.0` |
| `config_fingerprint` | `FactoryConfig.fingerprint()`: 12-char SHA-256 of the effective configuration, set on every history record (workflow records via `execution_record()`, and the resume check in `FactoryService`) |
| `summary`, `error` (redacted), `details` | human-readable summary; decision payload, warnings, next status |

Reproducibility (spec §68.16): factory, workflow, prompt, agent versions,
provider, model and per-call metrics are recorded per execution; human
decisions are in `decisions.md` / `decision_log`; the input requirements are in
`requirements-input.md`. The configuration itself is **not** stored in
history, but every workflow record carries its `config_fingerprint` (the same
12-char SHA-256 that `factory doctor` prints), so a change of configuration
between runs is visible. Git commits of generated code are recorded per task
(`development.completed_tasks`, traceability in `validation.json`) and for
the release (`release-notes.json`); test results are in `test-results.*`,
`validation.*` and the tool logs. Tool versions (`<program> --version` for
`git` and every program in the plan) are recorded by `prepare_development` in
`development.tool_versions` and in its history record.

## Resume and restart

Everything needed to resume is on disk:

```text
factory continue <id>
  → registry row (paused flag cleared)
  → compile graph, open projects/<id>/checkpoints/workflow-checkpoints.sqlite
  → get_state(thread_id=<id>): values, next nodes, pending interrupt
  → decide: start / nothing to do / resume mid-run / resume a legacy project
            ended at READY_FOR_DEVELOPMENT / resume clarification / tell user
  → before reporting a waiting project: _check_external_changes (below)
```

The generated code is part of the resumable state: `source/` (greenfield) or
the existing repository's `factory/<project-id>` branch holds every
committed task, and `development.completed_tasks` in the checkpoint records
which tasks are done, so a resumed build phase never redoes them.

The LLM's chat history is never used as context; each agent node rebuilds its
context from `FactoryState`. Details of each resume case are in
[the execution flow](../workflows/software_factory_execution_flow.md#14-failure-retry-pause-and-resume).

Spec §33 also asks to inspect the repository's Git state and validate
external changes on resume. `FactoryService.continue_` calls
`_check_external_changes` once the build phase has a `development.source_path`:
it compares the repository's HEAD with `development.last_commit` (set at
`prepare_development`, after every task/fix commit and after a retry commit)
and counts uncommitted files. Differences are reported in the status note
("External changes detected in <source>: …") and recorded as a `workflow`
history entry (stage `resume`). They are not blocked: they flow through the
next build, test, review and security checks. Releasing them unvalidated is
prevented separately by the release guard
([orchestration](orchestration_architecture.md#running-the-graph-coreworkflow_servicepy)).
Repository analysis itself still runs once, after requirements approval.

## Isolation

- Project ids match `^[a-z0-9][a-z0-9-]{0,62}$` (slugified from the name);
  `ProjectService.project_dir` also verifies the resolved path's parent is
  `projects/`. `../etc` is rejected with `InvalidProjectIdError`.
- One directory, one checkpoint file and one LangGraph thread per project.
- Clarification answers are only accepted for questions pending in that
  project's own snapshot.
- Covered by `test_projects_are_isolated` and
  `test_invalid_and_unknown_project_ids`.

## Planned — not implemented yet

- PostgreSQL or another multi-user backend (config accepts only
  `persistence.type: sqlite`).
