# Technology Selection Agent

**Status:** Implemented (design phase) · **Code:** [`src/software_factory/agents/technology_selection.py`](../../src/software_factory/agents/technology_selection.py) · **Spec:** §12, §73.2

## Agent Name

`technology-selection-agent` (version `1.1.0`), class `TechnologySelectionAgent`.

## Purpose

Choose the technology stack for **this** project from its approved requirements,
approved architecture and (for existing projects) repository facts — with rationale,
alternatives and risks. There is no default stack anywhere in the code or schema.

## Responsibilities

- Pick technologies per category only where the project needs that category (e.g. frontend, backend, database, messaging, cache, deployment, testing, observability).
- List deliberately omitted categories in `not_applicable` with a short reason.
- Give each choice a rationale, the alternatives considered and the requirement ids it serves.
- Weigh the factors in spec §12 (application type, NFRs, security, data, deployment, cost, complexity, ...).
- For existing repositories, prefer technologies compatible with the current stack and justify any replacement (`existing_project_compatibility`).
- State trade-offs and risks.
- Prefer technologies that can run on the build machine described in `environment` without extra infrastructure (for example an embedded database instead of a database server, unless the requirements need one). If a choice needs a program that is not installed, say so in `risks`.

## Inputs

| Key | Required | Source in workflow state | Notes |
|---|---|---|---|
| `requirements` | yes | `requirements` | Approved `RequirementAnalysis`. |
| `architecture` | yes | `architecture` | Approved `ArchitectureProposal`. |
| `repository_analysis` | no | `repository` | Existing-project mode only. |
| `environment` | no | `deps.environment` | Build-machine facts from `WorkflowService._environment`: `operating_system`, `allowed_commands` (= `security.allowed_commands`), `installed_commands`, `not_installed` (looked up with `find_program`, i.e. on the project's `PATH` without the factory's own virtual environment when `security.isolate_project_environment` is true — see the [shell tool](../tools/shell_tool.md#isolation-from-the-factorys-environment)), and a `note` that commands must start with an installed, allowed program and cannot use shell syntax. Example in [Implementation Planning Agent → Inputs](implementation_planning_agent.md#inputs). |
| `previous_technology_selection` | no | `technology_selection` | Previous output on re-runs. |

Plus `project_name` and `revision_feedback` (rejection reason for the `technology` stage).

## Input Schema

`AgentContext` with `stage="technology"`; payloads follow `RequirementAnalysis`,
`ArchitectureProposal`, `RepositoryAnalysis` and `TechnologySelection`, rendered as JSON
into the task prompt.

## Output

`TechnologySelection`, stored in state field `technology_selection` and persisted under
`<workspace>/projects/<project_id>/` as:

| File | Content |
|---|---|
| `technology-stack.json` | Machine-readable selection. |
| `technology-stack.md` | Human-readable render with revision number. |
| `artifacts/technology-stack.rev<N>.json` | Copy of revision `N`. |

## Output Schema

`TechnologySelection` ([`domain/technology.py`](../../src/software_factory/domain/technology.py)):

| Field | Type | Notes |
|---|---|---|
| `summary` | `str` | Required. |
| `choices` | `list[TechnologyChoice]` | **At least 1**. Each: `category`, `choice`, `rationale` (all required strings), `alternatives_considered: list[str]`, `requirement_ids: list[str]`. |
| `not_applicable` | `list[str]` | e.g. `"cache: not needed"`. |
| `tradeoffs` | `list[str]` | |
| `risks` | `list[str]` | |
| `existing_project_compatibility` | `str` | Default `""`. |

Note: the spec's example (§12) is keyed by category (`"frontend": {}`, ...); the code
uses a list of `choices` with a free-form `category`, so categories are not fixed.

## LLM Profile

`reasoning`, via `generate_structured`.

## Allowed Tools

None (`allowed_tools=()`).

## Prompt Location

- [`prompts/technology_selection/system.md`](../../prompts/technology_selection/system.md)
- [`prompts/technology_selection/task.md`](../../prompts/technology_selection/task.md) — placeholders `$project_name`, `$requirements`, `$architecture`, `$repository_analysis`, `$environment`, `$previous_technology_selection`, `$revision_feedback`.

Both prompts are version `1.1.0`. Each is
recorded as `<version>+<sha8>`.

## Workflow Stage

Node `select_technology`, lifecycle `TECHNOLOGY_SELECTION`, quality-gate / artifact key
`technology` (state field `technology_selection`).

```text
architecture_approval (approved) → select_technology → WAITING_FOR_TECHNOLOGY_APPROVAL
  → technology_approval ─┬─ rejected → select_technology
                         └─ approved → create_implementation_plan
```

## Preconditions

Entry criteria for `technology` ([`domain/quality_gates.py`](../../src/software_factory/domain/quality_gates.py)):

- "architecture is approved"
- "architecture artifact exists" (`state.architecture` is non-empty)

Also `run` fails if `requirements` or `architecture` is missing or empty (`None`, `""`, `[]` or `{}`).

## Postconditions

- `state.technology_selection` set; `artifact_revisions["technology"]` incremented; artifacts written.
- Lifecycle `WAITING_FOR_TECHNOLOGY_APPROVAL`; `failure` cleared; warnings and an `AGENT` execution record stored.

## Approval Requirements

Gate `technology_approval` (approval stage `technology`, artifact `technology-stack.md`):

- Approve → `IMPLEMENTATION_PLANNING`.
- Reject with `--reason` → `revision_feedback["technology"]`, back to `TECHNOLOGY_SELECTION`; the agent re-runs with the feedback and `previous_technology_selection`.

An approved change request from the [Coding Agent](coding_agent.md) with target
`technology` or `architecture` re-opens this stage, passing the request in as
`revision_feedback`.

This agent does not produce build/test commands. The
[Implementation Planning Agent](implementation_planning_agent.md) produces them in the
plan's `commands` section.

## Failure Conditions

Any `FactoryError` → `FAILED`, awaiting `factory retry`. An empty `choices` list or a
choice missing `rationale` is a schema error handled by the router's repair loop.

Deterministic `validate_output` rules:

| Rule | Result |
|---|---|
| The same category (case-insensitive, trimmed) chosen more than once | **Fail** (`AgentOutputError`) |
| A choice whose `rationale` is blank/whitespace | Warning |

## Retry Behavior

As for all design agents: LLM retries, structured-output repairs and capability-compatible
fallback in the router; `validate_output` failures go straight to `FAILED`; `factory retry`
re-runs the node. See [Requirement Agent → Retry Behavior](requirement_agent.md#retry-behavior).

## Dependencies

- `LLMClient`, `PromptLibrary`.
- Upstream: [Requirement Agent](requirement_agent.md), [Architecture Agent](architecture_agent.md), optional [repository analysis](../tools/repository_analysis_tool.md), and `environment` from [`core/workflow_service.py`](../../src/software_factory/core/workflow_service.py).
- Downstream: [Implementation Planning Agent](implementation_planning_agent.md).

## Examples

From `tests/support/fixtures.py`:

```json
{
  "summary": "Lightweight web stack.",
  "choices": [
    {"category": "backend", "choice": "FastAPI", "rationale": "Simple typed APIs",
     "requirement_ids": ["FR-1"]},
    {"category": "database", "choice": "SQLite", "rationale": "Small data volume"}
  ],
  "not_applicable": ["messaging: no async workloads"]
}
```

(Illustrative only — another project would get a different stack.)

## Known Limitations

- Categories are free text; `"db"` and `"database"` would not be detected as duplicates.
- `requirement_ids` on choices are not checked against the approved requirements.
- Choices are not checked against `environment`. A stack that needs an uninstalled program is caught only later, when `prepare_development` checks the plan's `commands`.
- Choices are not checked against the approved architecture or repository facts; compatibility is a prompt rule plus human review.
