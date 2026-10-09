# Software Factory Execution Flow

**This file is the human-readable source of truth for the workflow**
(spec §73.3). The executable version is `src/software_factory/orchestration/`
(`graph.py`, `routers/lifecycle_router.py`, `nodes/`). Update this file in
the same change whenever nodes, transitions or commands change.

Workflow version: `full-lifecycle-1.0` (`software_factory.WORKFLOW_VERSION`),
factory `0.2.0`.

Related: [orchestration architecture](../architecture/orchestration_architecture.md) ·
[greenfield workflow](greenfield_workflow.md) ·
[existing-project workflow](existing_project_workflow.md) ·
[change-request workflow](change_request_workflow.md)

## 1. What runs today

| Phase | Status |
|---|---|
| Design phase: requirements, clarification, repository analysis, architecture, technology, implementation plan, four approval gates | **Implemented** |
| Build phase: workspace preparation, per-task implement → build → test → commit loop, bounded debug loop, code review loop, security validation, final validation (with traceability), release approval gate, release (git tag) | **Implemented** |
| Change requests raised by the Coding Agent during development/fixing | **Implemented** (approve reopens a design stage; reject continues) |
| Impact analysis (spec §36, §68.7): existing projects after repository analysis; change requests before the human decides | **Implemented** ([Impact Analysis Agent](../agents/impact_analysis_agent.md)) |
| Failure handling (retry / cancel) for every working stage | **Implemented** |
| Resume check for external repository changes (spec §33) and a release guard that releases only the validated commit | **Implemented** |
| Plan environment check shown before plan approval and enforced before development | **Implemented** |
| Regression testing as a separate stage, custom workflow definitions (spec §68.8) | **Planned — not implemented yet** (one fixed graph; regressions are covered by planned tests for the impact analysis regression areas and the full test-suite runs) |

A successful run ends at `COMPLETED` with a `release-N` git tag.

## 2. Full lifecycle

```text
CREATE PROJECT ─► REQUIREMENTS ⇄ CLARIFICATION ─► REQUIREMENT APPROVAL
   ─► REPOSITORY ANALYSIS + IMPACT ANALYSIS (existing only; skipped for greenfield)
   ─► ARCHITECTURE ─► ARCHITECTURE APPROVAL
   ─► TECHNOLOGY ─► TECHNOLOGY APPROVAL
   ─► IMPLEMENTATION PLAN ─► PLAN APPROVAL
   ─► READY_FOR_DEVELOPMENT (prepare workspace)
   ─► for each task: DEVELOPMENT ─► BUILDING ─► TESTING ─► commit   (⇄ BUG_FIXING)
   ─► CODE REVIEW (⇄ fixes) ─► SECURITY VALIDATION (⇄ fixes)
   ─► FINAL VALIDATION (⇄ fixes) ─► RELEASE APPROVAL ─► RELEASE ─► COMPLETED

   DEVELOPMENT / BUG_FIXING ─► change request ─► impact analysis ─► CHANGE REQUEST APPROVAL
       ├─ approved → back to ARCHITECTURE / TECHNOLOGY / IMPLEMENTATION PLAN
       └─ rejected → back where the request came from
   any working stage ─► FAILED ─► retry (same stage) | cancel (CANCELLED)
```

## 3. LangGraph nodes

All 23 nodes are registered in `orchestration/graph.py`.

### Design phase

| Node | Type | Runs when status is | Produces |
|---|---|---|---|
| `load_project` | deterministic | (entry from `START`) | history entry; `CREATED → REQUIREMENTS_ANALYSIS` |
| `analyze_requirements` | agent: Requirement Agent | `REQUIREMENTS_ANALYSIS` | `requirements.md/.json`, questions or approval request |
| `wait_for_user_input` | human (interrupt `clarification`) | `WAITING_FOR_REQUIREMENT_CLARIFICATION` | stored answers; back to analysis |
| `requirement_approval` | human (interrupt `approval`) | `WAITING_FOR_REQUIREMENT_APPROVAL` | decision |
| `analyze_repository` | deterministic tool, then agent: Impact Analysis Agent (existing mode) | `REPOSITORY_ANALYSIS` | `repository-analysis.md/.json` and `impact-analysis.md/.json`, `state.impact_analysis` (existing mode) or "skipped" |
| `design_architecture` | agent: Architecture Agent | `ARCHITECTURE_ANALYSIS` | `architecture.md/.json` |
| `architecture_approval` | human (interrupt `approval`) | `WAITING_FOR_ARCHITECTURE_APPROVAL` | decision |
| `select_technology` | agent: Technology Selection Agent | `TECHNOLOGY_SELECTION` | `technology-stack.md/.json` |
| `technology_approval` | human (interrupt `approval`) | `WAITING_FOR_TECHNOLOGY_APPROVAL` | decision |
| `create_implementation_plan` | agent: Implementation Planning Agent | `IMPLEMENTATION_PLANNING` | `implementation-plan.md/.json` (tasks **and** project `commands`, plus the environment check); `state.plan_checks` |
| `plan_approval` | human (interrupt `approval`) | `WAITING_FOR_PLAN_APPROVAL` | decision |

Technology selection and planning also receive `environment` (OS, allowed
and installed commands) so the plan's commands can actually run. After the
plan is produced, `create_implementation_plan` runs the deterministic
environment check `development_nodes.command_problems(plan.commands, security)`
and stores the result as `state.plan_checks`. `implementation-plan.md` shows a
**"Commands the factory will run"** table and an **"Environment check"**
section (⚠️ with the problems, or "Every command's program is allowed and
installed."); `factory status` exposes `plan_checks`, and the CLI prints them
at the plan approval prompt.

For existing projects, architecture and planning also receive
`impact_analysis`; the planning prompt asks for tests that cover its
`regression_areas`.

### Build phase (`orchestration/nodes/development_nodes.py`, `orchestration/nodes/quality_nodes.py`)

`prepare_development`, `implement_task`, `build`, `test` and `debug` are in
`development_nodes.py`; `code_review`, `security_validation`,
`final_validation` and `release` are in `quality_nodes.py`; the gates and
`handle_failure` are in `human_nodes.py`.

| Node | Type | Runs when status is | Produces |
|---|---|---|---|
| `prepare_development` | deterministic | `READY_FOR_DEVELOPMENT` | checks entry gate, plan commands (`command_problems`); prepares the git repository; records tool versions; queues tasks |
| `implement_task` | agent: Coding Agent (`mode: implement`) | `DEVELOPMENT` | file changes applied to the repository, or a change request |
| `build` | deterministic tool | `BUILDING` | runs `setup` (if manifests changed), `format`, `lint`, `build` commands; `test-results.md/.json` on failure |
| `test` | deterministic tool | `TESTING` | checks the task's planned test files exist, runs `test` commands, `test-results.md/.json`; on pass: `git commit` |
| `debug` | agents: Testing Agent (for command failures) + Coding Agent (`mode: fix`) | `BUG_FIXING` | `test-analysis.md/.json`, fix applied, or a change request |
| `change_request_approval` | human (interrupt `approval`, stage `change_request`) | `WAITING_FOR_CHANGE_REQUEST_APPROVAL` | decision; reopens a design stage or continues |
| `code_review` | agent: Code Review Agent | `CODE_REVIEW` | `review.md/.json` (+ `artifacts/review.revN.json`) |
| `security_validation` | deterministic (secret scan, optional audit command) | `SECURITY_VALIDATION` | `security.md/.json`; `workflow` history record |
| `final_validation` | deterministic tool | `FINAL_VALIDATION` | `validation.md/.json` incl. traceability and `head_commit`; `workflow` history record |
| `release_approval` | human (interrupt `approval`, stage `release`) | `WAITING_FOR_RELEASE_APPROVAL` | decision |
| `release` | deterministic | `RELEASE` | annotated git tag `release-N`, `release-notes.md/.json` |
| `handle_failure` | human (interrupt `failure`) | `FAILED` | retry or cancel |

Agents: [requirement](../agents/requirement_agent.md),
[architecture](../agents/architecture_agent.md),
[technology selection](../agents/technology_selection_agent.md),
[implementation planning](../agents/implementation_planning_agent.md),
[coding](../agents/coding_agent.md), [testing](../agents/testing_agent.md),
[code review](../agents/code_review_agent.md). Tools:
[repository analysis](../tools/repository_analysis_tool.md),
[shell](../tools/shell_tool.md), [build](../tools/build_tool.md),
[test](../tools/test_tool.md), [git](../tools/git_tool.md),
[filesystem](../tools/filesystem_tool.md). Security validation is
deterministic code, not an agent.

## 4. Workflow diagram (mirrors `graph.py`)

Every node has the same conditional edge: `route_by_lifecycle` picks the next
node from the `lifecycle_status` the node just set. `⏸` = the graph stops at
`interrupt()`, the checkpoint is saved and the CLI command returns.

### 4.1 Design phase

```text
START
  ▼
load_project ─────────────────────────────── CREATED → REQUIREMENTS_ANALYSIS
  ▼
analyze_requirements ◄───────────────────────────────────────┐
  ├─ required question proposed AND round < max_rounds       │
  │    → WAITING_FOR_REQUIREMENT_CLARIFICATION               │
  │  wait_for_user_input ⏸ interrupt(clarification)          │
  │    ├─ all required answered → REQUIREMENTS_ANALYSIS ─────┤
  │    └─ missing answers → status unchanged → same node ⏸   │
  └─ clear (or round limit reached; questions → open issues) │
       → WAITING_FOR_REQUIREMENT_APPROVAL                    │
requirement_approval ⏸                                       │
  ├─ rejected (reason = feedback) → REQUIREMENTS_ANALYSIS ───┘
  └─ approved → REPOSITORY_ANALYSIS
analyze_repository   (greenfield: skipped, no artifact) → ARCHITECTURE_ANALYSIS
  existing: repository analysis → Impact Analysis Agent (existing_project)
            → impact-analysis.md/json; agent failure → FAILED (retry re-runs the node)
design_architecture → architecture_approval ⏸
  ├─ rejected → ARCHITECTURE_ANALYSIS
  └─ approved → TECHNOLOGY_SELECTION
select_technology → technology_approval ⏸
  ├─ rejected → TECHNOLOGY_SELECTION
  └─ approved → IMPLEMENTATION_PLANNING
create_implementation_plan (+ plan_checks) → plan_approval ⏸
  ├─ rejected → IMPLEMENTATION_PLANNING
  └─ approved → READY_FOR_DEVELOPMENT ─► prepare_development (same run)
```

### 4.2 Build phase

```text
prepare_development
  ├─ entry gate "development" unmet ──────────────────────────► FAILED
  ├─ plan has no valid `commands` → drop plan approval,
  │    feedback → IMPLEMENTATION_PLANNING (re-plan, approve again)
  ├─ command_problems(): shell disabled / cwd outside repo /
  │    forbidden / not allowlisted / not installed ───────────► FAILED
  ├─ repository preparation fails (existing: not git / dirty;
  │    greenfield: source/ is not the root of its own repo) ► FAILED
  ├─ tasks left → DEVELOPMENT
  └─ no tasks left (all already committed) → CODE_REVIEW

implement_task  (first task in task_queue)
  ├─ change request → WAITING_FOR_CHANGE_REQUEST_APPROVAL
  └─ changes applied → BUILDING
build   setup? format lint build ─┬─ pass → TESTING
                                  └─ fail → BUG_FIXING (budget left) | FAILED
test    planned test files missing → failed verification ─┐
        test ─┬─ fail ◄─────────────────────────────────────┘
              ├─ fail → BUG_FIXING (budget left) | FAILED
              └─ pass → git commit, reset debug_iterations
                   phase "task":  pop task → DEVELOPMENT (more tasks) | CODE_REVIEW
                   other phase:   PHASE_RETURN[phase]  (see §7)
debug   stuck check (command failure): same failure 3× ────────► FAILED "No progress: …"
        Testing Agent (command failure) → Coding Agent fix
          (command failure: up to limits.fix_candidates candidates,
           each applied + checked, first passing one kept)
  ├─ failure_category = environment ──────────────────────────► FAILED
  ├─ change request → WAITING_FOR_CHANGE_REQUEST_APPROVAL
  ├─ no candidate could be applied ───────────────────────────► FAILED
  └─ fix applied → BUILDING

change_request_approval ⏸
  ├─ approved → ARCHITECTURE_ANALYSIS | TECHNOLOGY_SELECTION | IMPLEMENTATION_PLANNING
  │             … design gates again … → READY_FOR_DEVELOPMENT → prepare_development
  └─ rejected → origin status (DEVELOPMENT or BUG_FIXING) with rejection note

code_review
  ├─ blocking findings AND review_rounds < max_review_rounds
  │    → phase "review" → BUG_FIXING → build → test → CODE_REVIEW
  └─ otherwise (limit reached → warning) → SECURITY_VALIDATION
security_validation
  ├─ blocking findings AND security_rounds < max_review_rounds
  │    → phase "security" → BUG_FIXING → build → test → SECURITY_VALIDATION
  ├─ blocking findings, rounds exhausted ─────────────────────► FAILED
  └─ passed → FINAL_VALIDATION
final_validation
  ├─ passed → WAITING_FOR_RELEASE_APPROVAL
  ├─ failed AND debug budget left → phase "validation" → BUG_FIXING → … → FINAL_VALIDATION
  └─ failed, budget exhausted ────────────────────────────────► FAILED
release_approval ⏸
  ├─ approve refused by FactoryService if HEAD != validation.head_commit
  │    or there are uncommitted changes other than validation.test_side_effects
  │    (status unchanged; reject to re-validate)
  ├─ approved → RELEASE
  └─ rejected → phase "release_feedback" → BUG_FIXING → build → test → CODE_REVIEW → …
release → git tag release-N, release-notes.md → COMPLETED → END

Failure branch — from any working node:
<working node> ── controlled error ──► FAILED
handle_failure ⏸ interrupt(failure)
  ├─ retry [--hint] → failure.resume_status → the node that failed
  │    (build phase: prepare_retry, §14; design stage: hint → revision_feedback)
  ├─ abort → CANCELLED → END
  └─ anything else → status unchanged → same node ⏸
```

## 5. Lifecycle states and transitions

Defined in `domain/lifecycle.py`. The LLM never sets a status: node code
decides and `transition()` rejects anything not listed below
(`InvalidTransitionError`). Every working stage may additionally go to
`FAILED`.

| From | Allowed to | Set by |
|---|---|---|
| `CREATED` | `REQUIREMENTS_ANALYSIS` | `load_project` |
| `REQUIREMENTS_ANALYSIS` | `WAITING_FOR_REQUIREMENT_CLARIFICATION`, `WAITING_FOR_REQUIREMENT_APPROVAL` | `analyze_requirements` |
| `WAITING_FOR_REQUIREMENT_CLARIFICATION` | `REQUIREMENTS_ANALYSIS` | `wait_for_user_input` |
| `WAITING_FOR_REQUIREMENT_APPROVAL` | `REQUIREMENTS_ANALYSIS` (reject), `REPOSITORY_ANALYSIS` (approve) | `requirement_approval` |
| `REPOSITORY_ANALYSIS` | `ARCHITECTURE_ANALYSIS` | `analyze_repository` |
| `ARCHITECTURE_ANALYSIS` | `WAITING_FOR_ARCHITECTURE_APPROVAL` | `design_architecture` |
| `WAITING_FOR_ARCHITECTURE_APPROVAL` | `ARCHITECTURE_ANALYSIS`, `TECHNOLOGY_SELECTION` | `architecture_approval` |
| `TECHNOLOGY_SELECTION` | `WAITING_FOR_TECHNOLOGY_APPROVAL` | `select_technology` |
| `WAITING_FOR_TECHNOLOGY_APPROVAL` | `TECHNOLOGY_SELECTION`, `IMPLEMENTATION_PLANNING` | `technology_approval` |
| `IMPLEMENTATION_PLANNING` | `WAITING_FOR_PLAN_APPROVAL` | `create_implementation_plan` |
| `WAITING_FOR_PLAN_APPROVAL` | `IMPLEMENTATION_PLANNING`, `READY_FOR_DEVELOPMENT` | `plan_approval` |
| `READY_FOR_DEVELOPMENT` | `DEVELOPMENT`, `CODE_REVIEW` (no tasks left), `IMPLEMENTATION_PLANNING` (plan without commands) | `prepare_development` |
| `DEVELOPMENT` | `BUILDING`, `WAITING_FOR_CHANGE_REQUEST_APPROVAL` | `implement_task` |
| `BUILDING` | `TESTING`, `BUG_FIXING` | `build` |
| `TESTING` | `DEVELOPMENT` (next task), `CODE_REVIEW`, `SECURITY_VALIDATION`, `FINAL_VALIDATION` (fix passed, per phase), `BUG_FIXING` | `test` |
| `BUG_FIXING` | `BUILDING`, `WAITING_FOR_CHANGE_REQUEST_APPROVAL` | `debug` |
| `WAITING_FOR_CHANGE_REQUEST_APPROVAL` | `ARCHITECTURE_ANALYSIS`, `TECHNOLOGY_SELECTION`, `IMPLEMENTATION_PLANNING` (approve), `DEVELOPMENT`, `BUG_FIXING` (reject) | `change_request_approval` |
| `CODE_REVIEW` | `SECURITY_VALIDATION`, `BUG_FIXING` | `code_review` |
| `SECURITY_VALIDATION` | `FINAL_VALIDATION`, `BUG_FIXING` | `security_validation` |
| `FINAL_VALIDATION` | `WAITING_FOR_RELEASE_APPROVAL`, `BUG_FIXING` | `final_validation` |
| `WAITING_FOR_RELEASE_APPROVAL` | `RELEASE` (approve), `BUG_FIXING` (reject) | `release_approval` |
| `RELEASE` | `COMPLETED` | `release` |
| `FAILED` | any working stage, `CANCELLED` | `handle_failure` |
| `COMPLETED` | `REQUIREMENTS_ANALYSIS` (only through `factory update`) | terminal until updated |
| `CANCELLED` | — | terminal |

- **Working stages** (may fail, may be retried): `REQUIREMENTS_ANALYSIS`,
  `REPOSITORY_ANALYSIS`, `ARCHITECTURE_ANALYSIS`, `TECHNOLOGY_SELECTION`,
  `IMPLEMENTATION_PLANNING`, `READY_FOR_DEVELOPMENT`, `DEVELOPMENT`,
  `BUILDING`, `TESTING`, `BUG_FIXING`, `CODE_REVIEW`, `SECURITY_VALIDATION`,
  `FINAL_VALIDATION`, `RELEASE`.
- **Waiting for a human:** the seven `WAITING_FOR_*` statuses
  (clarification, requirement, architecture, technology, plan, change request,
  release) plus `FAILED`.
- **Terminal** (router returns `END`): `COMPLETED`, `CANCELLED`.
  `READY_FOR_DEVELOPMENT` is no longer terminal: it routes to
  `prepare_development`.
- `REGRESSION_TESTING` no longer exists. Re-running the full test suite after
  every fix and in final validation covers regressions; for existing projects
  the plan also includes tests for the impact analysis `regression_areas`.

**Pause is not a lifecycle status.** It is a flag on the project registry row
(see §14).

### Router map (`routers/lifecycle_router.py`, `NODE_FOR_STATUS`)

| Status | Node | Status | Node |
|---|---|---|---|
| `REQUIREMENTS_ANALYSIS` | `analyze_requirements` | `DEVELOPMENT` | `implement_task` |
| `WAITING_FOR_REQUIREMENT_CLARIFICATION` | `wait_for_user_input` | `BUILDING` | `build` |
| `WAITING_FOR_REQUIREMENT_APPROVAL` | `requirement_approval` | `TESTING` | `test` |
| `REPOSITORY_ANALYSIS` | `analyze_repository` | `BUG_FIXING` | `debug` |
| `ARCHITECTURE_ANALYSIS` | `design_architecture` | `WAITING_FOR_CHANGE_REQUEST_APPROVAL` | `change_request_approval` |
| `WAITING_FOR_ARCHITECTURE_APPROVAL` | `architecture_approval` | `CODE_REVIEW` | `code_review` |
| `TECHNOLOGY_SELECTION` | `select_technology` | `SECURITY_VALIDATION` | `security_validation` |
| `WAITING_FOR_TECHNOLOGY_APPROVAL` | `technology_approval` | `FINAL_VALIDATION` | `final_validation` |
| `IMPLEMENTATION_PLANNING` | `create_implementation_plan` | `WAITING_FOR_RELEASE_APPROVAL` | `release_approval` |
| `WAITING_FOR_PLAN_APPROVAL` | `plan_approval` | `RELEASE` | `release` |
| `READY_FOR_DEVELOPMENT` | `prepare_development` | `FAILED` | `handle_failure` |
| `COMPLETED`, `CANCELLED` | `END` | unmapped status | `RuntimeError` |

## 6. Development: workspace, tasks and commands

### Workspace preparation (`prepare_development`)

1. `require_entry("development")` — all four design approvals and a plan.
2. The plan must validate as `ImplementationPlan` including `commands`
   (`test` is mandatory). A plan approved before `commands` existed fails
   validation: the plan approval is removed, `revision_feedback` asks the
   planner to add commands, and the status returns to
   `IMPLEMENTATION_PLANNING` — the plan must be approved again.
3. Command preflight (`command_problems`, the same check shown on the plan
   before approval): `security.shell_enabled` must be true; every command's
   `cwd` must be relative and inside the repository; no program named as
   `argv[0]` may be in the always-forbidden set; each must be in
   `security.allowed_commands` **and** installed (`find_program`, i.e.
   `shutil.which` on the project's `PATH`: without the factory's own virtual
   environment when `security.isolate_project_environment` is true). Otherwise
   → `FAILED` before any code is written ("The approved project commands
   need programs that are not available: …"; hint: install them / allow
   them, then `factory retry`).
4. Repository:
   - **greenfield:** `projects/<id>/source/` is created and made its own git
     repository: `git init` whenever `source/.git` is missing (the workspace
     may lie inside another git checkout, such as the factory's own), then
     `git rev-parse --show-toplevel` must be `source/` itself, else `FAILED`
     ("… is not the root of its own git repository"). A default `.gitignore`
     is written and, if there is no commit yet, an initial commit
     `chore: initialize repository (Software Factory)` is made;
   - **existing:** the repository must be a git repository with a clean
     working tree, else `FAILED`. The factory checks out (or creates) the
     branch `factory/<project-id>`; the branch you had checked out is left
     untouched.
   - on re-entry (after a change request) the recorded `source_path` is
     reused.
5. `task_queue` = plan tasks not yet in `completed_tasks`, in plan order but
   never before their `depends_on` tasks. Counters (`debug_iterations`,
   `review_rounds`, `security_rounds`) are reset, `phase = "task"`,
   `last_commit` = current HEAD.
6. Reproducibility: `<program> --version` is run for `git` and every program
   in the plan; the first line of each (or `unknown`) is stored in
   `development.tool_versions` and in the `workflow` / `development` history
   record. Every execution record also carries `config_fingerprint`
   (`FactoryConfig.fingerprint()`).

### Project commands

The approved plan's `commands` (`setup`, `format`, `lint`, `build`, `test`,
`package`, `audit`; each a list of `{argv, cwd}`) are the **only** commands
the build phase runs. Each step runs through its tool (`tool_for_step`:
`setup` → `PackageManagerTool`, `format` → `FormatterTool`, `lint` →
`LintTool`, `build` and `package` → `BuildTool`, `test` → `TestTool`,
`audit` → `DependencyAuditTool`; history records carry `details.tool`), each
a `ProjectCommandTool` on `ShellTool` (no shell, allowlist,
timeout, scrubbed env) inside the repository with `CI=true`, `NO_COLOR=1`,
`FORCE_COLOR=0`, `PYTHONDONTWRITEBYTECODE=1`. With
`security.isolate_project_environment: true` (default) the factory's own
virtual-environment `bin` directory is removed from `PATH` and `VIRTUAL_ENV`
/ `PYTHONHOME` are unset (`project_environment()` in `tools/shell.py`), so a
generated project never silently uses the factory's Python, pytest or other
tools. The same lookup decides which commands the Technology Selection and
Planning agents are told are installed. A step stops at its first
failing command. A command refused by policy at run time
(`ToolPermissionError`) is recorded as a failed run (exit code `None`), not
a crash. Pass/fail is only `exit_code == 0 and not timed_out`. The
full output of every command is saved as
`artifacts/tool-runs/NNN-<step>.log`; the last 4 000 characters go to agents.

`setup` runs only when the dependency-manifest fingerprint
(`package.json`, `pyproject.toml`, `requirements.txt`, lock files, …) differs
from the last successful setup.

### Per-task loop

```text
implement_task   Coding Agent gets: task, requirements, architecture,
                 technology, commands summary, file tree, relevant files
                 (task likely_files/tests first, then manifests, then the rest,
                 within limits.max_context_chars), change_request_feedback
                 → CodeChanges{summary, files[create|update|delete],
                               test_code[…] (the task's test files),
                               edits[{path, find, replace}], change_request?}
                 → all_files (files + test_code), then edits (find must occur exactly
                   once, else ChangeApplyError) applied with FilesystemTool, obtained via
                   ToolKit.require(FILESYSTEM, coding agent's allowed_tools)
                   (paths confined to the repo; no .git)
build            [setup] format lint build          (failure → test-results.md)
test             planned test files exist?          (path-like entries of task.tests)
                   no  → failed verification "(factory check) planned test files exist":
                         "The task's planned tests were not written. Missing test files: …"
                   yes → test → test-results.md
  pass           git add -A; git commit "<task id>: <title>"
                 completed_tasks += {task_id, commit, completed_at}
                 next task → DEVELOPMENT, or "development" stage done → CODE_REVIEW
  fail           → debug (see §7)
```

Code is committed only after the project's own tests pass, so each task is
one green commit. After every commit `development.last_commit` = HEAD. The
planned-test-files check exists because a test command may exit 0 while
running zero tests.

## 7. Fixing: debug loop, fix budget and phases

`state.development` holds `phase` (what a successful fix returns to) and
`fix_context` (what to fix: `source` = `verification` | `review` |
`security` | `validation` | `release_feedback`, plus details).

| `phase` | Set by | After a fix passes build + test (`PHASE_RETURN`) | Commit message |
|---|---|---|---|
| `task` | `prepare_development`, `implement_task` | task bookkeeping (next task or `CODE_REVIEW`) | `<task id>: <title>` |
| `review` | `code_review` | `CODE_REVIEW` | `fix(review): <summary>` |
| `security` | `security_validation` | `SECURITY_VALIDATION` | `fix(security): <summary>` |
| `validation` | `final_validation` | `FINAL_VALIDATION` | `fix(validation): <summary>` |
| `release_feedback` | release rejection | `CODE_REVIEW` | `fix(release_feedback): <summary>` |

`debug` node:

1. `debug_iterations += 1`.
2. **Stuck detection** (only for `fix_context.source = verification`, i.e. a
   failed command or the planned-test-files check): `_failure_signature`
   fingerprints the failure's error lines (lines with `Error`, `FAIL`,
   `assert`, `Exception` or `error:`; digits ignored) and appends it to
   `development.failure_history`. When the same signature has now occurred
   `NO_PROGRESS_LIMIT` = 3 times → `FAILED` "No progress: the same failure
   came back 3 times in a row after automatic fixes" (hint: read
   `test-results.md` and `test-analysis.md`, then
   `factory retry <project> --hint "…"`). On a second occurrence a
   `NO PROGRESS` note is added to the failure details ("The previous fix did
   not work — do not repeat it … the bug is probably in the code, not in the
   test").
3. For `verification`, the **Testing Agent** analyzes the failed step,
   command, exit code and output tail (plus `human_guidance` if a hint was
   given) → `test-analysis.md/.json` with a `failure_category`.
   `environment` (tool missing, service down, …) → `FAILED` immediately with
   the agent's suggested fix as hint — no fix attempt is wasted.
4. **Failed-attempt memory:** if the previous round's chosen fix
   (`development.pending_attempt`) did not cure the failure, it moves to
   `development.fix_attempts` with the new failure's last error line. The
   last 4 attempts are appended to the fix details as "EARLIER FIX ATTEMPTS
   FOR THIS FAILURE (all failed — do something different)".
5. The **Coding Agent** (`mode: fix`) gets the failure details (or the
   review/security/validation findings, or the release feedback), the
   analysis, `human_guidance` and the most relevant files (those mentioned in
   the output, the last changed files, the task's files). It returns changes
   or a change request.
6. **Fix candidates** (`limits.fix_candidates`, default and shipped 3; only
   for `verification` — review, security, validation and release-feedback
   fixes use 1): the Coding Agent is asked up to N times in the same round.
   Each candidate's files are snapshotted (`_snapshot` over
   `touched_paths`), the candidate is applied and really checked
   (`_check_steps`: `setup` if the manifests changed, `format`, `lint`,
   `build`, `test`, plus the planned-test-files check). The first candidate
   that passes is kept (history: "Fix candidate k of N passes; keeping it").
   A failing one is rolled back (`_restore`) and described to the next
   candidate as an earlier attempt; the last candidate is kept even if it
   fails, so the normal loop continues from it. A candidate that cannot be
   applied (`ChangeApplyError`, e.g. "Edit for X: the `find` text occurs N
   times") is rolled back and skipped, and the remaining candidates of the
   round may not use `edits` (`allow_edits: false`; small models repeat a
   failing edit) — they must return complete files. If the round ends with no
   candidate kept (the earlier ones failed and were rolled back, the last one
   could not be applied) → `FAILED` "None of the N proposed fixes could be
   applied", or "None of the N proposed fixes worked (X were tested and still
   failed, Y could not be applied)" (hint: retry, optionally with `--hint`).
7. Changes applied → `BUILDING`; the fix then goes through build and test
   again (the kept candidate is re-verified by the normal nodes and
   committed by `test`).

A passing test clears `failure_history`, `fix_attempts`, `pending_attempt`
and `human_hint`.

**Fix budget:** a failed build/test/final validation goes to `BUG_FIXING`
only while `debug_iterations < limits.max_debug_iterations` (default 5).
When exhausted → `FAILED` ("… still failing after N fix attempt(s)"; hint:
fix by hand or adjust the plan, then `factory retry`). One `debug` round
counts once, however many candidates it tried. A passing test resets
`debug_iterations` to 0, and `factory retry` restores the full budget (and
the review/security rounds, see §14).

## 8. Code review, security and final validation

**Code review** (`code_review`): entry gate `code_review` (task queue
empty). The Code Review Agent gets requirements, architecture, technology,
plan, the diff `base_commit..HEAD` (truncated to `max_context_chars`), the
file tree, the last test results and the previous review. Findings with
severity `critical`/`high` are blocking. With blocking findings and
`review_rounds < limits.max_review_rounds` (default 2) → fix cycle
(`phase = review`) and review again. When the round limit is reached with
blocking findings still open, the workflow **continues** with a warning; the
open findings are shown in `review.md` and `validation.md` for the release
decision.

**Security validation** (`security_validation`, deterministic):

- every tracked file: sensitive names (`.env`, `id_rsa`, `id_ed25519`,
  `credentials.json`, `*.pem`, `*.key`, `*.p12`, `*.pfx`) → `critical`;
- secret scan of file contents (private keys, `sk-`, AWS, GitHub, Google,
  Slack tokens → `critical`; hard-coded credentials → `high`, or `medium`
  in test code; placeholder values ignored);
- the plan's `audit` command if any (a failure is a `medium` finding;
  otherwise the report notes that no audit ran);
- `security`-category review findings are listed but do not block.

Blocking = `critical`/`high` non-review findings. With blocking findings and
`security_rounds < max_review_rounds` → fix cycle (`phase = security`);
when exhausted → `FAILED` (remove the secrets by hand, then retry). Every
run appends a `workflow` / `security_validation` history record.

**Final validation** (`final_validation`, deterministic): lists uncommitted
changes (`GitTool.changed_paths()`) **before** the run — any are a failure —
then re-runs `setup` (only if manifests changed), `lint`, `build`, `test`,
`package`, and checks that the task queue is empty. Files that the run itself
created or changed are stored as `validation.test_side_effects` and reported
as a warning ("The test run itself created or changed files (tests should use
temporary folders): …"). It
builds a **traceability** table per requirement: acceptance criteria →
planned tasks → commits → named tests, status `covered`, `not_planned`
(warning) or `not_implemented` (failure); requirements without a named test
are a warning. Open review findings are a warning. The report records
`head_commit` (the commit that was validated) and a `workflow` /
`final_validation` history record is appended. Pass →
`WAITING_FOR_RELEASE_APPROVAL`. Fail → fix cycle (`phase = validation`) while
the debug budget lasts, else `FAILED`.

## 9. Release

`release_approval` pauses with artifact `validation.md` (review
`validation.md`, `review.md`, `security.md`).

- `factory approve <id>` → `RELEASE`, but only if the code is exactly what
  final validation checked: `FactoryService._decide` calls
  `_require_validated_code`, which refuses with `WorkflowActionError` "The
  code changed after final validation, so it cannot be released as is" when
  the repository's HEAD differs from `validation.head_commit` or there are
  uncommitted changes other than the recorded `test_side_effects`. Hint:
  reject the release to re-validate.
- `factory reject <id> --reason "…"` → the reason becomes a fix (this is
  also how manual edits get re-validated: the fix cycle's `git add -A`
  commits them):
  `phase = release_feedback`, debug/review/security counters reset,
  `BUG_FIXING` → build → test → commit → **code review again** → security →
  final validation → release gate again.

`release` checks entry gate `release` (validation passed, security passed,
release approved), creates the next free annotated tag `release-N` in the
repository, writes `release-notes.md/.json` (tag, commit, release artifact,
repository) and sets `COMPLETED`.

## 10. CLI commands and what they drive

The local web UI (`factory ui`, see the [Web UI guide](../setup/web_ui_guide.md))
calls the same service methods from its buttons and forms; long actions run in
a background worker and the page polls the project status.

| Command | Service method | Effect on the workflow |
|---|---|---|
| `factory update <id> --text/--file` | `update` | Starts the next version of a `COMPLETED` app — see [updating a released app](#updating-a-released-app) |
| `factory delete <id> [--yes]` | `delete_project` | Removes the project from the register and deletes its workspace folder (documents, history, checkpoints, generated `source/`); refused while the project runs; never touches an existing-code repository. A project whose folder was removed by hand raises "folder … no longer exists" instead of being silently recreated |
| `factory ui [--port N] [--no-browser]` | — | Starts the local web UI on `127.0.0.1`; every page action maps to one of the methods below |
| `factory create <name> [-r TEXT \| -f FILE] [-d DESC] [--repository PATH]` | `create_project` | New project in `CREATED`; `--repository` sets mode `existing` |
| `factory requirements <id> (--text \| --file)` | `set_requirements` | Replace raw requirements; only before `start` |
| `factory start <id>` | `start` | Requires requirements; runs `load_project → …` until the first interrupt/END |
| `factory status <id>` | `status` | Read-only: status, completed stages, task progress, pending action, pending questions/approval; at the plan gate also the plan's environment-check problems (`plan_checks`) |
| `factory questions <id>` | `questions` | Read-only: pending questions + saved answers |
| `factory answer <id> <Qn> "<text>"` | `answer` | Saves the answer immediately; does not advance |
| `factory continue <id>` | `continue_` | Resume from wherever the project is (see §14); reports external changes to the generated repository |
| `factory approve <id> [--stage S] [--comment C]` | `approve` | Resumes the pending gate with `approved` (design gates, `change_request`, `release`); a release approval is refused if the code changed after final validation |
| `factory reject <id> --reason R [--stage S]` | `reject` | Resumes the pending gate with `rejected`; reason required |
| `factory retry <id> [--hint TEXT]` | `retry` | At `FAILED`: re-runs the failed stage with a fresh fix budget; `--hint` guides the agents (build phase: `development.human_hint` → Testing/Coding agents' `human_guidance`, cleared when the work passes; design stage: `revision_feedback` for that stage). If a run was interrupted: resumes from last checkpoint (a hint is refused with `WorkflowActionError` "A hint can only be given when retrying a failed project") |
| `factory cancel <id>` | `abort` | At `FAILED` only: → `CANCELLED` |
| `factory pause <id>` | `pause` | Sets pause flag; a running workflow stops after its current node |
| `factory history <id> [--json] [--limit N]` | `history` | Read-only: execution history |
| `factory evaluate [--case NAME]…` | — (`core/model_evaluation.run_evaluation`) | No workflow effect: runs real factory agents with the configured models on small fixed cases (`requirement_extraction`, `ambiguity_detection`, `architecture_reasoning`, `technology_fit`, `code_generation`), checks the answers with code (the code-generation case runs the generated tests), prints PASS/FAIL per case, saves `<workspace>/evaluations/evaluation-<timestamp>.json`, exits 1 if any case fails |
| `factory list`, `factory doctor [--probe]`, `factory version` | — | No workflow effect |

`--stage` accepts `requirements`, `architecture`, `technology`,
`implementation_plan`, `change_request`, `release` and refuses the decision if
the project is waiting at a different gate.

Which command moves which transition:

| Transition | Command |
|---|---|
| plan approved → `READY_FOR_DEVELOPMENT` → build phase | `factory approve` at the plan gate (the same run continues through development, review, security and validation until the next gate or failure) |
| `WAITING_FOR_CHANGE_REQUEST_APPROVAL` → design stage / origin | `factory approve --stage change_request` / `factory reject --stage change_request --reason …` |
| `WAITING_FOR_RELEASE_APPROVAL` → `RELEASE` / `BUG_FIXING` | `factory approve --stage release` (only for the validated, clean commit) / `factory reject --stage release --reason …` |
| `FAILED` → failed stage / `CANCELLED` | `factory retry` / `factory cancel` |
| legacy `READY_FOR_DEVELOPMENT` (ended) → `prepare_development` | `factory continue` |

A single command can therefore run for a long time (many LLM calls and
command runs); `factory pause` stops it after the current node.

## 11. Quality gates

`domain/quality_gates.py`. Nodes call `require_entry()` before doing work; a
failure raises `QualityGateError` → `FAILED`. Approval gates check the
**next** stage's criteria before advancing and stay put if they are unmet.

| Stage key | Entry criteria | Checked by |
|---|---|---|
| `requirements` | `requirements_input` is non-empty | `analyze_requirements` |
| `repository_analysis` | requirements approved **and** requirements artifact exists | `analyze_repository`, `requirement_approval` |
| `architecture` | requirements approved **and** requirements artifact exists | `design_architecture` |
| `technology` | architecture approved **and** architecture artifact exists | `select_technology`, `architecture_approval` |
| `implementation_plan` | technology approved **and** technology artifact exists | `create_implementation_plan`, `technology_approval` |
| `development` | all four design approvals **and** implementation plan exists | `prepare_development`, `plan_approval` |
| `code_review` | every planned task implemented (task queue empty) | `code_review` |
| `release` | final validation passed, security validation passed, release approved | `release`, `release_approval` |

Exit criteria come from real results, never from agent text: development
exits when every task's build and tests passed and it was committed; testing
when the `test` commands exit 0; security when no blocking deterministic
finding remains; final validation when the full re-run passes, the queue is
empty, git is clean and every planned requirement is implemented.

Agents also run deterministic output checks (`validate_output`) — duplicate
ids, dependency cycles, paths outside the repository or inside `.git`,
requirements not assessed by the review, etc. Hard violations raise
`AgentOutputError`: the model gets one correction attempt with the exact
reason (`LLMAgent.validation_repairs` = 1: "Your answer failed the factory's
checks: …"), then `FAILED`; soft ones become warnings.

## 12. Clarification and approval

**Clarification:**

```text
analyze_requirements
  drop proposed questions that repeat an already-answered one (similarity ≥ 0.85)
  any required AND clarification_round < limits.max_clarification_rounds (default 5)
    → ids Q1, Q2, … (counter continues across rounds), clarification_round += 1
    → WAITING_FOR_REQUIREMENT_CLARIFICATION
  otherwise → questions appended to requirements.open_questions (+ warning at limit)
    → WAITING_FOR_REQUIREMENT_APPROVAL
wait_for_user_input ⏸
```

`factory questions` / `factory answer <id> Q1 "…"` (saved immediately) /
`factory continue` (refused until every required question is answered).

**Approval / rejection** — every `*_approval` node stops with
`{kind: approval, stage, artifact, revision}`:

- `approve` → decision stored in `approvals[stage]` and `decision_log`, stage
  added to `completed_stages`, feedback cleared, status moves forward.
- `reject --reason "…"` → reason mandatory. Design gates: back to the
  producing agent with `revision_feedback[stage]`, producing revision N+1
  (`artifacts/<stem>.rev<N+1>.json`), then the same gate again. Release gate:
  fix cycle (§9). Change-request gate: see §13.
- Every decision is written to `decisions.md` and execution history
  (`kind: human_decision`).
- `factory continue` at a gate only reports "Waiting for a human approval
  decision."

## 13. Change-request flow

The Coding Agent may return a `change_request` (`target` =
`architecture` | `technology` | `implementation_plan`, `problem`,
`proposed_change`, `impact`) instead of files, in `implement_task` or
`debug`. Proposed file changes in the same response are not applied.

```text
implement_task / debug ── change_request
  → Impact Analysis Agent (change_request): requirements, architecture, plan,
      the request, completed tasks, changed files
      (failure → "Impact analysis failed; see errors.md", error added; not FAILED)
  → change-request.md/.json, state.change_request {…, task_id, origin_status,
                                                   completed_tasks, changed_files,
                                                   impact_analysis | impact_analysis_error}
  → WAITING_FOR_CHANGE_REQUEST_APPROVAL
change_request_approval ⏸ (stage change_request, artifact change-request.md)
  approved:
    target architecture        → ARCHITECTURE_ANALYSIS;   invalidate architecture, technology, plan
    target technology          → TECHNOLOGY_SELECTION;    invalidate technology, plan
    target implementation_plan → IMPLEMENTATION_PLANNING; invalidate plan
    revision_feedback[target] = "Approved change request … Problem … Proposed change … Reviewer note"
    invalidated stages, plus development, code_review, security_validation,
      final_validation, removed from completed_stages; phase = task
    → agent revises → gates again → plan approved → prepare_development
      (same repository; completed tasks are skipped)
  rejected (reason required):
    development.change_request_feedback = "…rejected; keep the approved decisions… Reviewer note"
    → origin status (DEVELOPMENT or BUG_FIXING); the Coding Agent retries with that note
```

`change-request.md` shows the agent's problem, proposed change,
"Impact (Coding Agent's assessment)", "## Impact analysis" (the Impact
Analysis Agent's result or the failure note), plus "What is affected so far": deterministic
facts (where it was raised, tasks already committed, files changed since
`base_commit`, from `git diff --name-only base..HEAD`).

Details: [change-request workflow](change_request_workflow.md).

## 14. Failure, retry, pause and resume

LLM-level (inside one agent call, automatic, see
[LLM architecture](../architecture/llm_architecture.md#model-router-llmrouterpy)):

```text
provider call ─ retryable error → retry same model (max_agent_retries, backoff)
             ─ invalid JSON / schema → repair turn (max_structured_output_repairs)
             ─ still failing, fallback-eligible → next capability-compatible model
             ─ otherwise → LLMError
```

Workflow-level (needs a human):

```text
node fails (LLMError, AgentOutputError, QualityGateError, FactoryError from a tool,
            fix budget exhausted, environment failure, security rounds exhausted, …)
  → execution record (failed) → errors[] + errors.md {stage, error_kind, message, hint}
  → failure = {…, resume_status: <working stage>} → FAILED → handle_failure ⏸
factory retry <id> [--hint "…"]
                     → resume_status → that node runs again
                       (build phase: adjusted by prepare_retry, below;
                        history: "Retry requested for stage S with human hint: …")
factory cancel <id>  → CANCELLED (terminal)
factory continue <id> at FAILED → only reports "Run `factory retry` after fixing."
```

| Situation | How to resume |
|---|---|
| Waiting at a gate or for answers | `approve` / `reject`, or `answer` + `continue` |
| `FAILED` | fix the cause (hint in `errors.md`; you may edit the code in the repository), then `retry`; or `cancel` |
| **Paused** (`factory pause`) | `factory continue` (`start`, `approve`, `reject`, `retry` also clear the flag) |
| Process killed / Ctrl-C mid-node | `factory continue` (or `retry`) re-runs the interrupted node |
| Machine restart | any command; state is loaded from the project's checkpoint |

**Retry in the build phase** (`handle_failure` → `development_nodes.prepare_retry`,
only when `state.development` exists):

```text
debug_iterations = review_rounds = security_rounds = 0      fresh budgets
failure_history = []                                         stuck detection restarts
--hint given → development.human_hint = hint                 Testing + Coding agents get it
                                                             as human_guidance until the
                                                             failing work passes
failed stage BUG_FIXING and fix_context.source == verification
    → resume at BUILDING instead, fix_context cleared        fresh build + test run,
                                                             so a fixed environment or a
                                                             manual code fix is re-checked
all tasks done (task_queue empty) and the repository is dirty
    → git commit "chore: manual changes before retry"        security / final validation
      development.last_commit = HEAD                         see a clean tree
otherwise → failure.resume_status
```

The retry's history entry notes what was done ("re-running build and tests",
"committed manual changes"). Manual edits made while tasks are still queued
are not committed by the retry; they are committed with the next green task.

`factory continue <id>` (`FactoryService.continue_`):

```text
clear pause flag
not started?                        → start
COMPLETED / CANCELLED               → "Nothing to continue."
next nodes pending, no interrupt    → resume from last checkpoint
READY_FOR_DEVELOPMENT, graph ended  → WorkflowService.resume_after(project, "plan_approval")
                                      (update_state(…, as_node="plan_approval"), then run:
                                       the router sends it to prepare_development)
_check_external_changes             (only once development.source_path exists)
waiting for clarification           → all required answered? resume with answers : error
waiting for failure decision        → note: use `factory retry`   (+ external-changes note)
waiting for approval                → note: approve or reject     (+ external-changes note)
```

**External changes (spec §33).** `_check_external_changes` compares the
generated repository's HEAD with `development.last_commit` (set at
`prepare_development`, after every task/fix commit and after a retry commit)
and counts uncommitted files, ignoring the files recorded as
`validation.test_side_effects`. If either differs, the status note starts with
"External changes detected in `<source>`: HEAD moved from … to …; N
uncommitted file(s). They will be built, tested and reviewed with the next
step." and a `workflow` history entry (stage `resume`, status `info`) is
recorded. The changes are not blocked; they flow through the next build,
test, review and security checks, and the release guard (§9) stops them
from being released unvalidated.

The `resume_after` branch exists for projects that finished the design phase
before the build phase existed: their graph ended at `READY_FOR_DEVELOPMENT`.
If such a plan has no `commands`, `prepare_development` sends it back to
`IMPLEMENTATION_PLANNING` with feedback (§6).

Pause is checked between nodes only; a long LLM call or command is not cut
short. Only one process can advance a project at a time (`fcntl` lock).
Ctrl-C prints a reminder to run `factory continue` and exits 130.

## 15. Greenfield vs existing project

| | Greenfield | Existing |
|---|---|---|
| Created with | `factory create <name> …` | `factory create <name> … --repository <path>` |
| `analyze_repository` | records "skipped", no artifact | scans the repository, writes `repository-analysis.md/.json` |
| Agent context | no repository facts | facts passed to Architecture, Technology Selection and Implementation Planning agents; impact analysis passed to Architecture and Implementation Planning agents |
| Code location | `projects/<id>/source/` (new git repository) | the given repository, branch `factory/<project-id>` |
| Precondition for development | — | must be a git repository with a clean working tree |
| Release tag | `release-N` in `source/` | `release-N` on the `factory/<project-id>` branch |
| Impact analysis | n/a | after repository analysis, writes `impact-analysis.md/.json` ([Impact Analysis Agent](../agents/impact_analysis_agent.md)) |

Details: [greenfield workflow](greenfield_workflow.md),
[existing-project workflow](existing_project_workflow.md).

## 16. What each node records

| Node | Execution history `kind` / `stage` |
|---|---|
| `load_project` | `workflow` / `start` |
| design agent nodes | `agent` / stage key (provider, model, prompt versions, `llm_calls`) |
| `analyze_repository` | `tool` / `repository_analysis`; existing mode also `agent` / `repository_analysis` (Impact Analysis Agent) |
| change request raised (`implement_task` / `debug`) | `agent` / `change_request` (Impact Analysis Agent, `SUCCESS` or `FAILED`) |
| `wait_for_user_input` | `clarification` / `requirements` |
| approval gates | `human_decision` / stage key (`release` for the release gate) |
| `change_request_approval` | `human_decision` / `change_request` |
| `create_implementation_plan` | `agent` / `implementation_plan` (plan checks go to state and `implementation-plan.md`) |
| `prepare_development` | `workflow` / `development` (task queue, tool versions) |
| `implement_task` | `agent` / `development`; `tool` / `apply_changes` |
| `debug` | `agent` / `bug_fixing` (Testing and Coding Agent); `tool` / `apply_changes` |
| `build`, `test`, `final_validation`, `security_validation` (audit) | `tool` / `setup`, `format`, `lint`, `build`, `test`, `package`, `audit` — one per command, with `details.tool`, `cwd` and log file |
| `code_review` | `agent` / `code_review` |
| `security_validation` | `workflow` / `security_validation` (passed/failed, finding count) |
| `final_validation` | `workflow` / `final_validation` (passed, or the failures) |
| `factory continue` with external changes | `workflow` / `resume` |
| `release` | `workflow` / `release` |
| `handle_failure` | `workflow` / failed stage |

Every record carries `config_fingerprint`, except the `resume` record written
by `FactoryService` outside the graph. After every node, `state.json`, `progress.md` (stage checklist + task
progress), `decisions.md`, `errors.md`, `project.md` and
`clarification-questions.md` are rewritten. See
[persistence](../architecture/persistence_architecture.md).

Tests: `tests/workflow/test_design_workflow.py` (design phase, restart,
pause, isolation) and `tests/workflow/test_build_workflow.py` (full build
phase to release, fix loop, fix budget + retry, environment failures, review
loop and round limit, secret blocking, change request approve/reject,
release rejection, missing programs, cwd outside the repository, existing
repository branch, dirty repository, path escape, external changes and the
release guard, missing planned test files).

## Updating a released app

A `COMPLETED` project can be changed later without creating a new project:
`factory update <id> --text "what to add or change"` or **Update this app** on
the web page (`POST /p/<id>/update`). `FactoryService.update`:

1. refuses anything but `COMPLETED`, and needs the app's code folder;
   uncommitted edits there are reported (as on `continue`) and flow through
   the next build, tests and review;
2. moves the finished version's documents to `versions/v<N>/` (the project
   logs `decisions.md`, `progress.md`, `errors.md`, `project.md` are copied and
   keep growing) — the web UI lists them under **Earlier versions**
   (`/p/<id>/doc/<name>?version=N`);
3. resets the per-version state — approvals, completed stages, review,
   security, validation, release, and the task bookkeeping (the code folder,
   setup fingerprint and tool versions are kept) — records the release in
   `versions`, sets `version = N+1` and `update_request`, and appends the change
   to `requirements_input`;
4. applies that as the `release` node's output (`WorkflowService.start_update`,
   `update_state(as_node="release")`), so the router continues at
   `REQUIREMENTS_ANALYSIS`.

The run is then the normal lifecycle, with every gate approved again:

| Stage | What is different for an update |
|---|---|
| Requirements, architecture, technology, plan | The agents get `update_request` ("released as version N, tag release-N; requested change: …") next to the previous approved artifact, and are told to keep what the change does not touch |
| Repository analysis | Runs on the released code (as for an existing project), followed by impact analysis |
| Development | Only the new plan's tasks; commits are prefixed `v<N> ` because task ids restart at T-1; the review diff starts at the released commit |
| Release | The next free tag: `release-2`, `release-3`, … |

Existing-code projects keep working on the `factory/<id>` branch. Merge it into
your main branch after each release; an update continues on that branch.
