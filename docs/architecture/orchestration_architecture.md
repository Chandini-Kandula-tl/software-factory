# Orchestration Architecture

How the LangGraph workflow is built, routed, paused and resumed. The
step-by-step lifecycle (every node, transition and CLI command) is in
[the execution flow](../workflows/software_factory_execution_flow.md); this
document explains the mechanics.

## Components

| File | Role |
|---|---|
| `orchestration/graph.py` | Declares the 23 nodes, wraps each with state mirroring, adds one conditional edge per node, compiles with a checkpointer. Its docstring holds the ASCII flow |
| `orchestration/routers/lifecycle_router.py` | `route_by_lifecycle`: next node = pure function of `lifecycle_status` (`NODE_FOR_STATUS`, `TERMINAL_STATUSES`) |
| `orchestration/nodes/agent_nodes.py` | Design agent nodes: `analyze_requirements`, `design_architecture`, `select_technology`, `create_implementation_plan` (also stores `plan_checks`) |
| `orchestration/nodes/deterministic_nodes.py` | `load_project`, `analyze_repository` |
| `orchestration/nodes/development_nodes.py` | Build phase: `prepare_development`, `implement_task`, `build`, `test`, `debug`; `prepare_retry`, `command_problems` and the shared helpers (`_run_commands`, `_verify`, `_apply_changes`, …) |
| `orchestration/nodes/quality_nodes.py` | Quality phase: `code_review`, `security_validation`, `final_validation`, `release`; `_traceability` |
| `orchestration/nodes/human_nodes.py` | Nodes that pause for a human: `wait_for_user_input`, the six approval gates (`requirement_approval`, `architecture_approval`, `technology_approval`, `plan_approval`, `release_approval` via `make_approval_gate`; `change_request_approval` via `make_change_request_gate`), `handle_failure` |
| `orchestration/nodes/common.py` | `Timer`, `execution_record()`, `failure_update()`, `fail_stage()`, `invoke_agent()` |
| `orchestration/state.py` | `FactoryState` TypedDict (JSON-serializable values) |
| `orchestration/ports.py` | `WorkflowRecorder` protocol and `WorkflowDependencies` |
| `domain/lifecycle.py` | `LifecycleStatus`, `WORKING_STAGES`, `WAITING_FOR_HUMAN`, `ALLOWED_TRANSITIONS`, `transition()` |
| `domain/quality_gates.py` | `ENTRY_CRITERIA`, `require_entry()`, `unmet_entry_criteria()` |
| `domain/development.py` | Build-phase schemas: `CodeChanges`, `ChangeRequest`, `TestFailureAnalysis`, `ReviewReport`, `CommandRun`, `VerificationResult`, `SecurityReport`, `ValidationReport`, `TraceRow`, `ReleaseRecord`, `ImpactAnalysis` |
| `core/workflow_service.py` | Runs/resumes the compiled graph (streaming, pause, run lock, `resume_after`) |

## Graph construction

```text
build_workflow(deps):
  for each (name, node) in nodes:
      graph.add_node(name, _persisting(deps, node, name))
      graph.add_conditional_edges(name, route_by_lifecycle, ROUTE_TARGETS)
  graph.add_edge(START, "load_project")
compile_workflow(deps, checkpointer) → graph.compile(checkpointer=checkpointer)
```

There are no static edges other than `START → load_project`. Every node has the
same conditional edge, so topology is fully defined by the node → status
decisions plus the router table.

`_persisting` wraps each node: after the node returns its update, it merges the
update into the state, sets `current_stage` to the node name, and calls
`deps.recorder.persist_state(merged)` so `state.json`, `progress.md`, etc. are
refreshed after every node (see [persistence](persistence_architecture.md)).

## Routing

```text
route_by_lifecycle(state):
  status in {COMPLETED, CANCELLED} → END
  otherwise → NODE_FOR_STATUS[status]
  unmapped status → RuntimeError
```

Every non-terminal status maps to exactly one node; the full table is in
[the execution flow](../workflows/software_factory_execution_flow.md#router-map-routerslifecycle_routerpy-node_for_status).
`READY_FOR_DEVELOPMENT` routes to `prepare_development`; it used to be
terminal.

## Lifecycle state machine

The LLM never sets a status. Node code decides and validates every change with
`transition(current, target)`, which raises `InvalidTransitionError` if the
pair is not in `ALLOWED_TRANSITIONS`. The full table is in
[the execution flow](../workflows/software_factory_execution_flow.md#5-lifecycle-states-and-transitions).

`WORKING_STAGES` (may go to `FAILED`; `FAILED` may return to any of them or
go to `CANCELLED`): the five design working stages plus
`READY_FOR_DEVELOPMENT`, `DEVELOPMENT`, `BUILDING`, `TESTING`, `BUG_FIXING`,
`CODE_REVIEW`, `SECURITY_VALIDATION`, `FINAL_VALIDATION`, `RELEASE`.
`CHANGE_REQUEST_TARGETS` = `ARCHITECTURE_ANALYSIS`, `TECHNOLOGY_SELECTION`,
`IMPLEMENTATION_PLANNING`.

## Node patterns

### Agent call (`common.invoke_agent`)

```text
context = agent.build_context(only declared inputs, revision_feedback)
result  = agent.run(context)                 LLMClient.generate_structured + validate_output
          (validate_output raises AgentOutputError → one correction turn with the exact
           reason, LLMAgent.validation_repairs = 1, then the error propagates)
  FactoryError → execution record (failed, llm_calls) → failure_update → FAILED
success → execution record (agent, provider, model, prompt versions, llm_calls)
```

Design agent nodes additionally check `require_entry(stage)`, bump
`artifact_revisions[stage]`, write `<stem>.json/.md` + revision copy and set
the stage's `WAITING_FOR_*_APPROVAL` status. `analyze_requirements` decides
deterministically whether to wait for clarification (required question
proposed and `clarification_round < limits.max_clarification_rounds`).

### Deterministic nodes

- `load_project`: `CREATED → REQUIREMENTS_ANALYSIS`.
- `analyze_repository`: existing mode → `analyze_repository(path)` and
  `repository-analysis.{json,md}`, then the Impact Analysis Agent (mode
  `existing_project`, via `invoke_agent`) → `state.impact_analysis` and
  `impact-analysis.{json,md}`; greenfield → "skipped". Always
  `ARCHITECTURE_ANALYSIS`; `FactoryError`/`OSError` or an agent failure →
  `FAILED` (retry re-runs the whole node).
- A change request raised in `implement_task` / `debug` runs the Impact
  Analysis Agent (mode `change_request`) in `_change_request_update`; if it
  fails, the error is appended to `errors` but the project does not go to
  `FAILED`: the request still reaches `WAITING_FOR_CHANGE_REQUEST_APPROVAL`.

### Build-phase nodes (`development_nodes.py`, `quality_nodes.py`)

`development_nodes.py` holds `prepare_development`, `implement_task`, `build`,
`test`, `debug` and the helpers; `quality_nodes.py` holds `code_review`,
`security_validation`, `final_validation` and `release` and imports the shared
helpers from `development_nodes`.

Tools are constructed per call from `deps.security` and the recorded
`source_path` through `ToolKit(source, security)`:

- project commands run through the step's `ProjectCommandTool`
  (`tool_for_step(step, toolkit.shell())`: `setup` → `PackageManagerTool`,
  `format` → `FormatterTool`, `lint` → `LintTool`, `build`/`package` →
  `BuildTool`, `test` → `TestTool`, `audit` → `DependencyAuditTool`), all on a
  `ShellTool` with tool name `project-command`, allowlist
  `security.allowed_commands`, `security.command_timeout_seconds` and
  `security.shell_enabled`, and the environment from
  `project_environment(security.isolate_project_environment)` (factory venv
  removed from `PATH`, `VIRTUAL_ENV`/`PYTHONHOME` unset). The `TOOL` history
  record carries `details.tool`;
- the Coding Agent's files and targeted `edits` are written with
  `toolkit.require(FILESYSTEM, coding agent's allowed_tools)` (raises
  `ToolPermissionError` "not allowed here" if the agent did not declare it);
  an edit whose `find` text is missing or not unique raises
  `ChangeApplyError`;
- `GitTool` is built directly (same `enabled` switch).

Command results become `CommandRun` / `VerificationResult`; pass/fail is
`exit_code == 0 and not timed_out`.

`prepare_development` first re-checks the plan's commands with
`command_problems(commands, security)` (shell disabled, cwd outside the repo,
forbidden program, program not allowlisted, program not installed) and fails
before any code is written if there is a problem. It records
`<program> --version` for `git` and every plan program in
`development.tool_versions` and in its history record. "Installed" means
`find_program()` finds it on the project's `PATH` (the same isolated lookup
that produces the `environment` facts for the Technology Selection and Planning
agents). For greenfield projects it makes `source/` its own git repository
(`git init` when `source/.git` is missing, then `rev-parse --show-toplevel`
must be `source/`), because the workspace may sit inside another checkout. The same check runs in
`create_implementation_plan` and is stored as `state["plan_checks"]` so the
human sees it before approving the plan.

`test` first checks that the current task's planned test files (entries of
`task.tests` that look like paths) exist; if not, it records a failed
verification (`argv = ["(factory check) planned test files exist"]`) without
running the test command, which goes through the normal fix loop.

`debug` (the bug-fixing loop) for a build/test failure
(`fix_context.source == "verification"`):

```text
signature = _failure_signature(details)   error lines, digits → '#', sha1[:12]
failure_history += signature
  count ≥ NO_PROGRESS_LIMIT (3) → FAILED "No progress: …" (hint: retry --hint)
  count > 1                     → details += "NO PROGRESS: …"
Testing Agent (human_guidance = development.human_hint)
  environment → FAILED
pending_attempt (last round's chosen fix) → fix_attempts (+ last error line)
for k in 1..limits.fix_candidates:
  Coding Agent (fix_details + "EARLIER FIX ATTEMPTS FOR THIS FAILURE …", human_guidance)
  change_request → change-request gate
  _snapshot(touched_paths); _apply_changes
    ChangeApplyError → _restore, remember, allow_edits = False, next candidate
  _verify(_check_steps: setup if manifests changed, format, lint, build, test;
          + planned-test-files check)
    pass → keep ("Fix candidate k of N passes; keeping it"), stop
    fail and not last → _restore, remember outcome, next candidate
    fail and last → keep it (the normal loop continues)
no candidate kept → FAILED "None of the N proposed fixes could be applied" /
                    "None of the N proposed fixes worked (X … tested, Y … not applied)"
pending_attempt = chosen fix → BUILDING (build and test re-verify, test commits)
```

Review, security, validation and release-feedback fixes skip the signature,
the Testing Agent and the attempt memory, and use one candidate.

`final_validation` lists uncommitted paths (`GitTool.changed_paths()`)
before its run (a failure) and after it; the difference is stored as
`validation.test_side_effects` (a warning). `FactoryService`'s release guard
and resume external-change check ignore exactly those paths.

`state["development"]` is the build-phase bookkeeping:

| Key | Meaning |
|---|---|
| `source_path`, `base_commit` | repository being built; HEAD when development started (diff base for review and for a change request's `changed_files`) |
| `last_commit` | HEAD the factory last produced: set by `prepare_development`, after every task/fix commit and after a retry commit. Compared on resume to detect external changes |
| `task_queue`, `completed_tasks` | remaining task ids (dependency-ordered); `{task_id, commit, completed_at}` per committed task |
| `phase` | what a successful fix returns to: `task`, `review`, `security`, `validation`, `release_feedback` |
| `fix_context` | what `debug` must fix: `{source: verification, verification}` or `{source: review/security/validation, findings}` or `{source: release_feedback, feedback}`, plus `summary` |
| `debug_iterations` | fix rounds since the last green test (one per `debug` run, however many candidates); capped by `limits.max_debug_iterations` |
| `failure_history` | signatures of build/test failures since the last green test (stuck detection); cleared by a passing test and by `prepare_retry` |
| `fix_attempts`, `pending_attempt` | failed fixes for the current failure (`{summary, files, result}`) and the fix chosen in the last round, not yet judged; shown to the Coding Agent; cleared by a passing test |
| `human_hint` | text from `factory retry --hint`; passed to the Testing and Coding agents as `human_guidance`; cleared by a passing test |
| `review_rounds`, `security_rounds` | fix rounds after review / security; each capped by `limits.max_review_rounds` |
| `setup_fingerprint` | hash of dependency manifests at the last successful `setup` |
| `tool_versions` | `{program: first line of "<program> --version"}` for `git` and every plan program, recorded by `prepare_development` |
| `last_changes` | files written by the last agent response (context for `debug`) |
| `change_request_feedback` | rejection note for the Coding Agent's next call |

`PHASE_RETURN` maps `phase` to the status `test` moves to after a green fix:
`review → CODE_REVIEW`, `security → SECURITY_VALIDATION`,
`validation → FINAL_VALIDATION`, `release_feedback → CODE_REVIEW`; `task`
uses task bookkeeping (next task or `CODE_REVIEW`). `_fix_or_fail` sends a
failed verification to `BUG_FIXING` while the fix budget lasts, else to
`FAILED`.

### Human nodes

Each calls LangGraph `interrupt(payload)` first. The graph stops; the
checkpoint holds the pending interrupt and the process may exit. Core later
resumes with `Command(resume=value)`; LangGraph re-runs the node and
`interrupt()` returns `value`.

| Node | Interrupt payload | Accepted resume value | Result |
|---|---|---|---|
| `wait_for_user_input` | `{kind: clarification, round, questions}` | `{answers: {Qn: {answer, answered_at}}}` with every required question answered | → `REQUIREMENTS_ANALYSIS` |
| design gates, `release_approval` (`ApprovalGate`) | `{kind: approval, stage, artifact, revision}` | `{decision: approved\|rejected, reason, decided_by, timestamp}` | approved → `on_approve` (only if the next stage's entry criteria hold); rejected → `on_reject` with `revision_feedback[stage] = reason`, plus `on_reject_update` (release: start a `release_feedback` fix cycle) |
| `change_request_approval` | `{kind: approval, stage: change_request, artifact: change-request.md, revision: 1, target}` | same as above | approved → reopen target stage, drop its and later approvals, and remove the target and later design stages plus `development`, `code_review`, `security_validation`, `final_validation` from `completed_stages`; rejected → `origin_status` with `change_request_feedback` |
| `handle_failure` | `{kind: failure, stage, error_kind, message, hint, resume_status, …}` | `{action: retry, hint?}` or `{action: abort}` | retry → `resume_status`, adjusted in the build phase by `development_nodes.prepare_retry` (below); without `development`, a hint becomes `revision_feedback[failed stage]`; history "Retry requested for stage S [with human hint: …]"; abort → `CANCELLED` |

`prepare_retry(deps, state, target, hint="")` (called only when `state.development`
exists) resets `debug_iterations`, `review_rounds`, `security_rounds` and
`failure_history`; stores a non-empty hint as `development.human_hint`;
turns a retry of `BUG_FIXING` for a build/test failure
(`fix_context.source == "verification"`) into `BUILDING` with `fix_context`
cleared (a fresh build + test run); and, once the task queue is empty,
commits uncommitted manual edits in the repository as
`chore: manual changes before retry` (and updates `development.last_commit`). It returns the new target, the
updated `development` and a note for the history entry.

**Invalid resume payloads** (missing required answers, unknown decision,
unknown action, unmet next-stage criteria) make the node return `{}`. The
status is unchanged, so the router sends the workflow straight back into the
same node, which interrupts again.

## State (`FactoryState`)

Identity: `project_id`, `project_name`, `project_mode`, `repository_path`,
`factory_version`, `workflow_version`.
Progress: `lifecycle_status`, `current_stage`, `completed_stages`.
Clarification: `requirements_input`, `clarification_round`,
`question_counter`, `pending_questions`, `clarifications`.
Design artifacts: `requirements`, `repository`, `impact_analysis` (existing
projects), `architecture`, `technology_selection`, `implementation_plan` (all
`model_dump` dicts), and
`plan_checks` (the plan's environment-check problems, empty when fine).
Human decisions: `approvals` (latest per stage), `decision_log`,
`revision_feedback`, `artifact_revisions`.
Build phase:

| Field | Content |
|---|---|
| `development` | bookkeeping dict (table above) |
| `last_verification` | latest `VerificationResult` (build, test or final run) |
| `test_analysis` | latest `TestFailureAnalysis` from the Testing Agent |
| `change_request` | pending request `{target, problem, proposed_change, impact, task_id, origin_status, raised_at, summary, completed_tasks, changed_files, impact_analysis \| impact_analysis_error}` (`changed_files` = `git diff --name-only base_commit..HEAD`; `impact_analysis` from the Impact Analysis Agent, or `impact_analysis_error` if it failed), `None` otherwise |
| `review` | `{report, passed, blocking_findings, round}` |
| `security` | `SecurityReport` |
| `validation` | `ValidationReport` (incl. `traceability` and `head_commit`, the commit that passed) |
| `release` | `ReleaseRecord` (`tag`, `commit`, `release_artifact`, `repository`, `released_at`) |

Errors: `failure`, `errors`, `warnings`, `last_execution_id`.

Values are JSON so checkpoints and `state.json` stay portable; nodes
re-validate into domain models when they need typed access.

## Ports

The workflow never imports persistence. It receives `WorkflowDependencies`:

```text
agents              FactoryAgents (8 agents, built by agents.catalog.build_agents)
recorder            WorkflowRecorder: persist_state, write_stage_artifact,
                    record_execution, write_tool_output(name, content) → relative path
limits              LimitsConfig
security            SecurityConfig (allowlist, timeout, shell_enabled)
analyze_repository  Callable[[Path], RepositoryAnalysis]
factory_version, workflow_version
source_root         where a greenfield project's code is generated (projects/<id>/source)
config_fingerprint  FactoryConfig.fingerprint(), stamped on every ExecutionRecord
environment         build-machine facts for technology selection and planning:
                    OS, allowed_commands, installed_commands, not_installed, note
```

`core/project_recorder.ProjectRecorder` implements `WorkflowRecorder`;
`core/workflow_service.WorkflowService` builds the dependencies.

## Running the graph (`core/workflow_service.py`)

- **Per-project isolation:** a fresh graph is compiled per call with a
  `SqliteSaver` on `projects/<id>/checkpoints/workflow-checkpoints.sqlite`
  and `thread_id = project_id`.
- **Run lock:** `fcntl.flock(LOCK_EX | LOCK_NB)` on `checkpoints/run.lock`.
- **Streaming:** `graph.stream(input, config, stream_mode="updates")`. After
  each update the project's `paused` flag is re-read; if set, the loop
  breaks after the current node.
- **Snapshot:** `graph.get_state()` gives `values`, `next` nodes and pending
  interrupts. `waiting_for` = interrupt `kind`; `interrupted_mid_run` = next
  nodes exist but no interrupt is pending.
- **Resume:** `resume(project, value)` streams `Command(resume=value)`;
  `resume(project)` streams `None` (continue from the last checkpoint).
- **Resume after a newer workflow added stages:** `resume_after(project, node)`
  calls `update_state(config, {}, as_node=node)` on a graph that already
  ended, then streams. `FactoryService.continue_` uses it with
  `as_node="plan_approval"` for projects that ended at
  `READY_FOR_DEVELOPMENT` under the old design-only workflow; the router then
  sends them to `prepare_development`.
- **External changes on resume (spec §33):** `FactoryService.continue_` calls
  `_check_external_changes` before reporting a waiting project. It compares
  the generated repository's HEAD with `development.last_commit` and counts
  uncommitted files; if either differs it adds "External changes detected in
  <source>: …" to the status note and appends a `WORKFLOW` history entry
  (stage `resume`). Nothing is blocked: the changes flow through the next
  build, test, review and security checks.
- **Release guard:** approving the `release` gate goes through
  `FactoryService._decide` → `_require_validated_code`, which refuses with
  `WorkflowActionError` "The code changed after final validation, so it
  cannot be released as is" when HEAD differs from `validation.head_commit`
  or there are uncommitted changes other than `validation.test_side_effects`.
  Rejecting the release starts a fix cycle that
  re-runs build, test, review, security and final validation (manual edits
  are committed by the fix cycle's `add -A`).

## Failure and crash semantics

- Controlled failures (`FactoryError`, including `LLMError`,
  `AgentOutputError`, `QualityGateError`, `ToolPermissionError` from file
  writes, and the build phase's own errors: missing programs, dirty
  repository, fix budget exhausted, environment problems, security rounds
  exhausted) become `FAILED` with an `errors` entry and
  `failure.resume_status`. A project command refused by `ShellTool` policy
  is not an exception: `_run_commands` records it as a failed run.
- Uncontrolled exceptions or a killed process abort the node before its
  checkpoint is written; `factory continue` re-runs it. A build-phase node
  interrupted mid-way may have already written files or run commands; the
  re-run starts from the node's inputs (e.g. `build` simply builds again).
- `_persisting` writes the Markdown/JSON mirror before LangGraph saves the
  checkpoint; a crash in that window can leave files one node ahead of the
  checkpoint (and a duplicate history entry after the re-run).

## Planned — not implemented yet

- Reusable workflow definitions (spec §68.8): there is one hard-coded graph,
  versioned as `WORKFLOW_VERSION = "full-lifecycle-1.0"`.
- Impact analysis as its own node. Today the Impact Analysis Agent runs
  inside `analyze_repository` (existing projects) and `_change_request_update`
  (change requests) via `invoke_agent`.
- Subgraphs; the build phase uses the same flat, status-routed graph.
