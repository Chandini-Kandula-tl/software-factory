# Implementation Planning Agent

**Status:** Implemented (design phase) · **Code:** [`src/software_factory/agents/implementation_planning.py`](../../src/software_factory/agents/implementation_planning.py) · **Spec:** §13, §68.13, §68.15, §73.2

## Agent Name

`implementation-planning-agent` (version `1.2.0`), class `ImplementationPlanningAgent`.

## Purpose

Convert the approved requirements, architecture and technology selection into an
ordered, traceable implementation plan with milestones, tests, a named release artifact
and the exact **project commands** (setup, lint, build, test, ...). It is the last
design-phase agent. Approving its plan moves the project to `READY_FOR_DEVELOPMENT`, and
the build phase runs the approved commands and nothing else.

## Responsibilities

- Break the work into small tasks with stable ids `T-1`, `T-2`, ...
- For each task: description, dependencies, requirement ids and acceptance-criteria ids it implements, likely files/modules, and the tests that prove it.
- Produce a dependency order that can actually be executed (no cycles).
- Group tasks into milestones.
- Name the release artifact (source repo, package, container image, ...) consistent with the approved technology.
- Provide `commands`: exact argv lists (no shell syntax such as `&&`, pipes or `cd`), each with an optional `cwd` relative to the repository root. Every program must be an allowed command from `environment`. `test` is mandatory and must exit 0 only when all tests pass (non-interactive, no watch mode).
- Make the first task create the project skeleton (manifest, test configuration, one passing smoke test) so the test command can run.
- Not change approved architecture or technology; if a decision looks non-viable, say so in `risks`.

## Inputs

| Key | Required | Source in workflow state | Notes |
|---|---|---|---|
| `requirements` | yes | `requirements` | Approved `RequirementAnalysis`. |
| `architecture` | yes | `architecture` | Approved `ArchitectureProposal`. |
| `technology_selection` | yes | `technology_selection` | Approved `TechnologySelection`. |
| `repository_analysis` | no | `repository` | Existing-project mode only. |
| `impact_analysis` | no | `impact_analysis` | Existing-project mode only: `ImpactAnalysis` from the [Impact Analysis Agent](impact_analysis_agent.md). The task prompt asks the agent to plan tests covering its `regression_areas`, so the existing behaviour is re-checked by the `test` command. |
| `environment` | no | `deps.environment` | Facts about the build machine, built by `WorkflowService._environment` (see below). |
| `previous_implementation_plan` | no | `implementation_plan` | Previous output on re-runs. |

`environment` is computed from `security.allowed_commands` when the workflow starts:

```json
{
  "operating_system": "Darwin arm64",
  "allowed_commands": ["git", "python", "python3", "pip", "pytest", "ruff", "uv", "node", "npm", "npx"],
  "installed_commands": ["git", "python3", "pip", "node", "npm", "npx"],
  "not_installed": ["python", "pytest", "ruff", "uv"],
  "note": "Project commands may only start with an installed, allowed command. Shell syntax (&&, |, cd) is not available."
}
```

Plus `project_name` and `revision_feedback` (rejection reason for `implementation_plan`).

## Input Schema

`AgentContext` with `stage="implementation_plan"`; payloads follow the schemas of the
upstream agents and `ImplementationPlan`, rendered as JSON.

## Output

`ImplementationPlan`, persisted under `<workspace>/projects/<project_id>/` as:

| File | Content |
|---|---|
| `implementation-plan.json` | Machine-readable plan. |
| `implementation-plan.md` | Human-readable render with revision number, including a **"Commands the factory will run"** table (step, command, directory) and an **"Environment check"** section: "⚠️ Environment check" with the list of problems (approving then means development stops at once), or "Every command's program is allowed and installed." |
| `artifacts/implementation-plan.rev<N>.json` | Copy of revision `N`. |

## Output Schema

`ImplementationPlan` ([`domain/planning.py`](../../src/software_factory/domain/planning.py)):

| Field | Type | Notes |
|---|---|---|
| `summary` | `str` | Required. |
| `release_artifact` | `str` | Required. |
| `commands` | `ProjectCommands` | **Required.** See below. |
| `milestones` | `list[Milestone]` | `name`, `task_ids`. |
| `tasks` | `list[ImplementationTask]` | **At least 1**. Each: `id`, `title`, `description` (required); `depends_on`, `requirement_ids`, `acceptance_criteria_ids`, `likely_files`, `tests` (lists of `str`). |
| `risks` | `list[str]` | |

`ProjectCommands` ([`domain/planning.py`](../../src/software_factory/domain/planning.py)).
Each step is a `list[ProjectCommand]`, and each `ProjectCommand` is
`{argv: list[str] (at least 1), cwd: str = "."}` ([`domain/development.py`](../../src/software_factory/domain/development.py)).

| Step | Required | Used by the build phase |
|---|---|---|
| `setup` | no | Installs dependencies. Runs before format/lint/build only when the dependency manifests changed. |
| `format` | no | Build step. |
| `lint` | no | Build step and final validation. |
| `build` | no | Build step and final validation. |
| `test` | **yes (at least 1)** | Test step and final validation. Its exit code is the only evidence that tests pass. |
| `package` | no | Final validation (builds the release artifact). |
| `audit` | no | [Security validation](security_validation_agent.md) (dependency audit). |

How the commands are run is described in the [build tool](../tools/build_tool.md) doc.

## LLM Profile

`reasoning`, via `generate_structured`.

## Allowed Tools

None (`allowed_tools=()`).

## Prompt Location

- [`prompts/implementation_planning/system.md`](../../prompts/implementation_planning/system.md)
- [`prompts/implementation_planning/task.md`](../../prompts/implementation_planning/task.md) — placeholders `$project_name`, `$requirements`, `$architecture`, `$technology_selection`, `$repository_analysis`, `$impact_analysis`, `$environment`, `$previous_implementation_plan`, `$revision_feedback`.

`system.md` is version `1.2.0` (keep tasks small — one behaviour or requirement
each, with its own tests — and make the first task only the project skeleton so
the test command can run; from the first live build run, 2026-09-30),
`task.md` version `1.2.0` (adds the impact-analysis section and the request to
plan tests for the regression areas). Each is
recorded as `<version>+<sha8>`.

## Workflow Stage

Node `create_implementation_plan`, lifecycle `IMPLEMENTATION_PLANNING`, quality-gate /
artifact key `implementation_plan`.

```text
technology_approval (approved) → create_implementation_plan → WAITING_FOR_PLAN_APPROVAL
  → plan_approval ─┬─ rejected → create_implementation_plan
                   └─ approved → READY_FOR_DEVELOPMENT → prepare_development (build phase)
```

After the agent returns, the node runs the deterministic environment check
`development_nodes.command_problems(plan.commands, security)` and stores the result in
`state["plan_checks"]`. It reports: shell disabled; a `cwd` outside the repository; a
forbidden program; a program not in `security.allowed_commands`; a program not
installed. The problems appear in `implementation-plan.md`, in `factory status` (field
`plan_checks`), and the CLI prints them at the plan approval prompt ("⚠ The plan's
commands cannot run on this machine: …", then "Reject the plan with what to change, or
install the programs first.").

`prepare_development` enforces the same check: any problem fails the stage before any
code is written. If the stored plan predates `commands` and cannot be validated, the plan
approval is removed and the workflow returns to `IMPLEMENTATION_PLANNING` with feedback
asking for `commands`.

## Preconditions

Entry criteria for `implementation_plan` ([`domain/quality_gates.py`](../../src/software_factory/domain/quality_gates.py)):

- "technology is approved"
- "technology artifact exists" (`state.technology_selection` non-empty)

Also `run` fails if any of the three required inputs is missing or empty (`None`, `""`, `[]` or `{}`).

## Postconditions

- `state.implementation_plan` set; `state.plan_checks` set (empty list when fine); `artifact_revisions["implementation_plan"]` incremented; artifacts written.
- Lifecycle `WAITING_FOR_PLAN_APPROVAL`; `failure` cleared; warnings and an `AGENT` execution record stored.

## Approval Requirements

Gate `plan_approval` (approval stage `implementation_plan`, artifact `implementation-plan.md`):

- An approved change request with target `implementation_plan`, `technology` or `architecture` (from the [Coding Agent](coding_agent.md)) also re-opens this stage. The request is passed in as `revision_feedback`.
- Approve → `READY_FOR_DEVELOPMENT`, **only if** the `development` entry criteria hold: all four approval stages (`requirements`, `architecture`, `technology`, `implementation_plan`) are approved. Otherwise the gate stays put.
- Reject with `--reason` → `revision_feedback["implementation_plan"]`, back to `IMPLEMENTATION_PLANNING`; the agent re-runs with the feedback and `previous_implementation_plan`.

## Failure Conditions

Any `FactoryError` → `FAILED`, awaiting `factory retry`. An empty `tasks` list, a missing
`release_artifact`, missing `commands`, an empty `commands.test` or an empty `argv` is a
schema error handled by the router's repair loop.

Deterministic `validate_output` rules:

| Rule | Result |
|---|---|
| Duplicate task ids | **Fail** |
| Dependency cycle among known tasks (reported as `T-1 -> T-2 -> T-1`) | **Fail** |
| A task depends on an unknown task id | Warning (unknown deps are ignored for cycle detection) |
| A task has no `requirement_ids` | Warning |

## Retry Behavior

As for all design agents: router retries / repairs / fallback; `validate_output`
failures go straight to `FAILED`; `factory retry` re-runs the node. See
[Requirement Agent → Retry Behavior](requirement_agent.md#retry-behavior).

## Dependencies

- `LLMClient`, `PromptLibrary`.
- Upstream: [Requirement](requirement_agent.md), [Architecture](architecture_agent.md) and [Technology Selection](technology_selection_agent.md) agents; optional [repository analysis](../tools/repository_analysis_tool.md) and [impact analysis](impact_analysis_agent.md) (existing projects).
- `environment` from `WorkflowService._environment` ([`core/workflow_service.py`](../../src/software_factory/core/workflow_service.py)).
- Downstream: the build phase: the [Coding Agent](coding_agent.md) (tasks and commands), the build/test steps, the [Testing Agent](testing_agent.md), and final validation (traceability from `requirement_ids` / `acceptance_criteria_ids` / `tests`).

## Examples

From `tests/support/fixtures.py`:

```json
{
  "summary": "Two milestones.",
  "release_artifact": "Source repository with a runnable web service",
  "commands": {"test": [{"argv": ["python", "-c", "print('ok')"]}]},
  "milestones": [{"name": "M1", "task_ids": ["T-1", "T-2"]}],
  "tasks": [
    {"id": "T-1", "title": "Scaffold", "description": "Create project skeleton",
     "requirement_ids": ["FR-1"]},
    {"id": "T-2", "title": "Employee CRUD", "description": "Implement CRUD",
     "depends_on": ["T-1"], "requirement_ids": ["FR-1"], "tests": ["test_crud"]}
  ]
}
```

The calculator plan used by the build-phase tests (`calculator_plan()`):

```json
{
  "summary": "Calculator library.",
  "release_artifact": "Source repository",
  "commands": {
    "test": [{"argv": ["python", "-m", "unittest", "discover", "-s", "tests", "-v"]}]
  },
  "tasks": [
    {"id": "T-1", "title": "Add function", "description": "add(a, b)",
     "requirement_ids": ["FR-1"], "acceptance_criteria_ids": ["AC-1"], "tests": ["tests/test_calc.py"]},
    {"id": "T-2", "title": "Subtract function", "description": "sub(a, b)",
     "depends_on": ["T-1"], "requirement_ids": ["FR-2"], "tests": ["tests/test_calc.py"]}
  ]
}
```

A fuller `commands` block for a Node project might look like this (illustrative):

```json
{
  "setup": [{"argv": ["npm", "install"]}],
  "lint": [{"argv": ["npx", "eslint", "."]}],
  "test": [{"argv": ["npm", "test", "--", "--watchAll=false"]}],
  "audit": [{"argv": ["npm", "audit", "--audit-level=high"]}]
}
```

A plan where `T-1` depends on `T-2` and `T-2` on `T-1` fails with
`Task dependencies contain a cycle: T-1 -> T-2 -> T-1`.

## Known Limitations

- `requirement_ids` and `acceptance_criteria_ids` are not checked against the approved requirements; only "task traces to nothing" is warned.
- Milestone `task_ids` are not validated against `tasks`.
- Covering the impact analysis `regression_areas` with tests is a prompt request only; it is not checked.
- `likely_files` / `tests` are free text. `tests` only feed traceability; they are not run individually (the `test` command runs everything).
- `validate_output` does not check `commands`, and the model is not asked to repair them. Allowlisted and installed programs, a disabled shell, forbidden programs and any `cwd` that escapes the repository are reported on the plan (`plan_checks`) for the human, and enforced by `prepare_development`, which fails the project before any code is written.
