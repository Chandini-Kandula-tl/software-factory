# Impact Analysis Agent

**Status:** Implemented (design and build phase) · **Code:** [`src/software_factory/agents/impact_analysis.py`](../../src/software_factory/agents/impact_analysis.py) · **Spec:** §36, §68.7, §73.2

## Agent Name

`impact-analysis-agent` (version `1.0.0`), class `ImpactAnalysisAgent`.

## Purpose

Explain what a change touches **before a human approves it**: which requirements,
components, files and tasks are affected, which existing behaviour must be
regression-tested, the risks, and a recommendation. Raw facts (repository
analysis, completed tasks, changed files) come from deterministic tools; the
agent only interprets them. It never decides anything — the workflow code and
the human do (see [Workflow Stage](#workflow-stage)).

It is used in two situations, selected by the input `mode`:

| Mode | When | Spec |
|---|---|---|
| `existing_project` | A new requirement on an existing repository, after the deterministic repository analysis and before architecture. | §68.7 |
| `change_request` | The Coding Agent raised a change request during development, before the human decides. | §36 |

## Responsibilities

- Base every statement on the inputs; name requirement ids (`FR-x` / `NFR-x`), components, files and task ids that actually appear in them.
- List the existing behaviour that must be regression-tested after the change (`regression_areas`).
- State risks honestly and give a clear recommendation for the human.
- Not design the solution — that happens in the next stage (architecture, or the revised design stage after an approved change request).

These rules live in the system prompt; the deterministic check in
[Failure Conditions](#failure-conditions) enforces the part that can be checked.

## Inputs

The agent receives an `AgentContext` built by `LLMAgent.build_context`, which copies
**only** the agent's declared input keys (and drops `None` values) from what the
workflow node offers. It never sees the whole project state.

| Key | Required | `existing_project` source | `change_request` source |
|---|---|---|---|
| `mode` | yes | `"existing_project"` | `"change_request"` |
| `requirements` | yes | `requirements` (approved) | `requirements` |
| `repository_analysis` | no | the `RepositoryAnalysis` just produced | — |
| `change_request` | no | — | the Coding Agent's `ChangeRequest` |
| `architecture` | no | — | `architecture` (approved) |
| `implementation_plan` | no | — | `implementation_plan` (approved) |
| `completed_tasks` | no | — | task ids already completed and committed |
| `changed_files` | no | — | `git diff --name-only base_commit..HEAD` (empty if no base commit) |

Plus `project_name`. `revision_feedback` is always `None` (the agent is not
re-run on rejection).

## Input Schema

`AgentContext` ([`agents/base.py`](../../src/software_factory/agents/base.py)):

```text
project_id: str
project_name: str
stage: str                    # "repository_analysis" or "change_request"
inputs: dict[str, Any]        # only the keys in the table above
revision_feedback: str | None # always None
```

`repository_analysis` follows `RepositoryAnalysis` (see the
[repository analysis tool](../tools/repository_analysis_tool.md));
`change_request` follows `ChangeRequest` (`target`, `problem`, `proposed_change`,
`impact`; see the [Coding Agent](coding_agent.md#output-schema)).

Non-string inputs are rendered into the task prompt as indented JSON; inputs not
offered in the current mode render as `None.`

## Output

Structured `ImpactAnalysis`. Where it goes depends on the mode:

| Mode | State | Files under `<workspace>/projects/<project_id>/` |
|---|---|---|
| `existing_project` | `state.impact_analysis` | `impact-analysis.json` and `impact-analysis.md` (latest only, no `rev<N>` copy) |
| `change_request` | `state.change_request.impact_analysis` | rendered as the `## Impact analysis` section of `change-request.md` (`change-request.json` holds only the Coding Agent's `ChangeRequest`) |

`impact-analysis.md` is rendered by `render_impact_analysis`
([`persistence/markdown.py`](../../src/software_factory/persistence/markdown.py)):
summary, recommendation, affected requirements / components / files / tasks,
"Must be regression-tested" and "Risks".

## Output Schema

`ImpactAnalysis` ([`domain/development.py`](../../src/software_factory/domain/development.py)):

| Field | Type | Notes |
|---|---|---|
| `summary` | `str` | Required. |
| `affected_requirements` | `list[str]` | Requirement ids (`FR-x`, `NFR-x`). |
| `affected_components` | `list[str]` | |
| `affected_files` | `list[str]` | |
| `affected_tasks` | `list[str]` | Task ids (mainly `change_request` mode). |
| `regression_areas` | `list[str]` | Existing behaviour that must be re-tested because of this change. |
| `risks` | `list[str]` | |
| `recommendation` | `str` | Required. What the human should decide and why. |

## LLM Profile

`reasoning`. The request is sent through `LLMClient.generate_structured`, so the chosen
model must also declare the `structured_output` capability. See
[LLM architecture](../architecture/llm_architecture.md). Live-checked on 2026-09-29
with `qwen2.5:14b`: sensible output, no warnings.

## Allowed Tools

None (`allowed_tools=()`). The agent cannot touch the file system, git or a shell;
the changed files and repository facts are gathered by deterministic code before it
runs.

## Prompt Location

- [`prompts/impact_analysis/system.md`](../../prompts/impact_analysis/system.md)
- [`prompts/impact_analysis/task.md`](../../prompts/impact_analysis/task.md) — placeholders `$project_name`, `$mode`, `$requirements`, `$change_request`, `$architecture`, `$implementation_plan`, `$repository_analysis`, `$completed_tasks`, `$changed_files`.

Both files have front matter version `1.0.0`. Execution history records each prompt as
`<version>+<sha8>`. `factory doctor` checks that both files exist (`PROMPT_DIRS` in
[`core/doctor.py`](../../src/software_factory/core/doctor.py)).

## Workflow Stage

The agent has no node of its own; it runs inside two existing nodes via
`invoke_agent` ([`orchestration/nodes/common.py`](../../src/software_factory/orchestration/nodes/common.py)).

**1. Existing project** — node `analyze_repository`
([`orchestration/nodes/deterministic_nodes.py`](../../src/software_factory/orchestration/nodes/deterministic_nodes.py)),
lifecycle status `REPOSITORY_ANALYSIS`, execution-history stage `repository_analysis`.

```text
requirement_approval → analyze_repository
    deterministic repository analysis → repository-analysis.md/json
    existing project? → Impact Analysis Agent (mode existing_project) → impact-analysis.md/json
  → ARCHITECTURE_ANALYSIS → design_architecture (receives impact_analysis)
  → … → create_implementation_plan (receives impact_analysis; plans tests for regression_areas)
```

Greenfield projects skip both the repository analysis and the agent.

**2. Change request** — `_change_request_update`
([`orchestration/nodes/development_nodes.py`](../../src/software_factory/orchestration/nodes/development_nodes.py)),
called from `implement_task` / `debug` when the Coding Agent returns a
`change_request`; execution-history stage `change_request`.

```text
Coding Agent → change_request
  → Impact Analysis Agent (mode change_request)
  → change-request.md/json (with "## Impact analysis")
  → WAITING_FOR_CHANGE_REQUEST_APPROVAL → change_request_approval ⏸
```

See the [existing-project workflow](../workflows/existing_project_workflow.md),
the [change-request workflow](../workflows/change_request_workflow.md) and the
[execution flow](../workflows/software_factory_execution_flow.md).

## Preconditions

- `existing_project`: `project_mode` is `existing`, a `repository_path` is set, the
  quality-gate entry criteria for `repository_analysis` are met and the deterministic
  analysis succeeded.
- `change_request`: the Coding Agent returned a `change_request`.
- Both: `LLMAgent.run` fails if the required inputs `mode` or `requirements` are
  missing or empty.

## Postconditions

On success:

- `existing_project`: `state.impact_analysis` holds the output; `impact-analysis.md/json` are written; the lifecycle moves to `ARCHITECTURE_ANALYSIS` with `repository_analysis` in `completed_stages`.
- `change_request`: `state.change_request.impact_analysis` holds the output; `change-request.md/json` are written; lifecycle `WAITING_FOR_CHANGE_REQUEST_APPROVAL`.
- An `AGENT` execution record (agent name and version, provider, model, profile, prompt versions, every LLM attempt, warnings) is appended to `execution-history.json`.

## Approval Requirements

The agent has no approval gate of its own. Its output is reviewed as part of:

- `existing_project`: the next gates (`architecture_approval`, … `plan_approval`), whose artifacts were produced with the impact analysis as input. `impact-analysis.md` sits next to them for the reviewer.
- `change_request`: `change_request_approval` — the human reads `change-request.md` (Coding Agent's problem, proposed change and own impact text, the impact analysis, and the deterministic "What is affected so far") and approves or rejects.

## Failure Conditions

| Mode | On any `FactoryError` from the agent |
|---|---|
| `existing_project` | The project moves to `FAILED` at stage `repository_analysis` (error recorded in `errors.md`, `resume_status = REPOSITORY_ANALYSIS`). The repository-analysis artifacts are already written. |
| `change_request` | The change request **still reaches the human**: `change-request.md` shows "Impact analysis failed; see errors.md" in the `## Impact analysis` section, the error is appended to `errors`, and a `FAILED` agent record is written to history. The project does not go to `FAILED`. |

| Cause | Error |
|---|---|
| Required input missing | `AgentOutputError` |
| Prompt file missing | `ConfigurationError` |
| LLM failure after retries/fallbacks/repairs | `LLMError` |

Deterministic `validate_output` rules:

| Rule | Result |
|---|---|
| `affected_requirements` contains ids not in the requirements (checked only when requirements have ids) | Warning |

## Retry Behavior

- **LLM layer:** the standard router behaviour — retries on retryable errors, structured-output repair calls and capability-compatible fallback models (see [Requirement Agent](requirement_agent.md#retry-behavior) and [`llm/router.py`](../../src/software_factory/llm/router.py)).
- **Workflow layer (`existing_project`):** `factory retry <project>` re-runs `analyze_repository` — the deterministic analysis and then the agent.
- **Workflow layer (`change_request`):** not retried; the human decides without it. Approving or rejecting proceeds normally.

## Dependencies

- `LLMClient` (the `ModelRouter`) and `PromptLibrary`, injected via `build_agents` in [`agents/catalog.py`](../../src/software_factory/agents/catalog.py) (`FactoryAgents.impact_analysis`).
- Upstream: [Requirement Agent](requirement_agent.md) (requirements), the [repository analysis tool](../tools/repository_analysis_tool.md), the [Coding Agent](coding_agent.md) (change request), the [git tool](../tools/git_tool.md) (changed files).
- Downstream consumers: [Architecture Agent](architecture_agent.md) and [Implementation Planning Agent](implementation_planning_agent.md) (optional input `impact_analysis`, existing projects); the human at the change-request gate.

## Examples

Output (from `tests/support/fixtures.py`, used by
`tests/workflow/test_design_workflow.py::test_existing_project_runs_repository_analysis`
and the change-request tests in `tests/workflow/test_build_workflow.py`):

```json
{
  "summary": "Touches the notes module and its tests.",
  "affected_requirements": ["FR-1"],
  "affected_components": ["CLI"],
  "affected_files": ["src/main.py"],
  "regression_areas": ["Existing note listing"],
  "risks": ["Changing storage format"],
  "recommendation": "Proceed; keep the file format backwards compatible."
}
```

`test_change_request_still_reaches_the_human_if_impact_analysis_fails` covers the
failure path in `change_request` mode.

## Known Limitations

- In `change_request` mode the analysis is not retried if it fails (the request still reaches the human; the error is in `errors.md`). When it succeeds it is written both to `impact-analysis.md/json` and into `change-request.md`. Warnings (e.g. unknown requirement ids) go to `state.warnings` and history.
- `impact-analysis.md/json` keep only the latest version (no `artifacts/impact-analysis.rev<N>.json`).
- Only requirement ids are checked; component, file and task names are not verified against the inputs.
- In `existing_project` mode the agent sees the deterministic repository summary, not file contents, so its assessment is coarse for large code bases.
- Requirement-level change requests and human-initiated change requests do not exist, so the agent is not run for them.
