# Architecture Agent

**Status:** Implemented (design phase) · **Code:** [`src/software_factory/agents/architecture.py`](../../src/software_factory/agents/architecture.py) · **Spec:** §11, §73.2

## Agent Name

`architecture-agent` (version `1.1.0`), class `ArchitectureAgent`.

## Purpose

Design the application architecture from **approved** requirements: components,
boundaries, data flow, integrations, security and scalability concerns, alternatives
and trade-offs. It is technology-aware but deliberately does not lock in products or
frameworks — that is the [Technology Selection Agent](technology_selection_agent.md)'s job.

## Responsibilities

- Choose an architecture style (e.g. modular monolith, client-server, event-driven).
- Identify components with responsibilities, interfaces, and the requirement ids each satisfies.
- Describe boundaries, data flow, external integrations, security and scalability considerations.
- List advantages, risks and trade-offs; propose at least one alternative where reasonable, with the reason it was not chosen.
- For existing repositories, prefer incremental change over replacement.
- Prefer the simplest architecture that meets the requirements.

## Inputs

Selected by `build_context` (declared keys only, `None` dropped):

| Key | Required | Source in workflow state | Notes |
|---|---|---|---|
| `requirements` | yes | `requirements` | Approved `RequirementAnalysis` (JSON). |
| `repository_analysis` | no | `repository` | `RepositoryAnalysis` facts; present only in existing-project mode. |
| `impact_analysis` | no | `impact_analysis` | `ImpactAnalysis` of the new requirement on the existing system, from the [Impact Analysis Agent](impact_analysis_agent.md); present only in existing-project mode. |
| `previous_architecture` | no | `architecture` | Previous proposal, passed on re-runs (rejection / retry). |

Plus `project_name` and `revision_feedback` (rejection reason for the `architecture` stage).

## Input Schema

`AgentContext` (`project_id`, `project_name`, `stage="architecture"`, `inputs`,
`revision_feedback`). Input payloads follow `RequirementAnalysis`
([`domain/requirements.py`](../../src/software_factory/domain/requirements.py)),
`RepositoryAnalysis` ([`domain/repository.py`](../../src/software_factory/domain/repository.py)),
`ImpactAnalysis` ([`domain/development.py`](../../src/software_factory/domain/development.py))
and `ArchitectureProposal` (below). They are rendered into the task prompt as JSON.

## Output

`ArchitectureProposal`, persisted under `<workspace>/projects/<project_id>/` as:

| File | Content |
|---|---|
| `architecture.json` | Machine-readable proposal. |
| `architecture.md` | Human-readable render with revision number. |
| `artifacts/architecture.rev<N>.json` | Copy of revision `N`. |

## Output Schema

`ArchitectureProposal` ([`domain/architecture.py`](../../src/software_factory/domain/architecture.py)):

| Field | Type | Notes |
|---|---|---|
| `style` | `str` | Required. |
| `overview` | `str` | Required. |
| `components` | `list[ArchitectureComponent]` | **At least 1** (`min_length=1`). Each: `name`, `responsibility`, `interfaces: list[str]`, `requirement_ids: list[str]`. |
| `boundaries` | `list[str]` | |
| `data_flow` | `list[str]` | |
| `external_integrations` | `list[str]` | |
| `security_considerations` | `list[str]` | |
| `scalability_considerations` | `list[str]` | |
| `advantages` | `list[str]` | |
| `risks` | `list[str]` | |
| `alternatives` | `list[ArchitectureAlternative]` | `name`, `summary`, `reason_not_chosen`. |
| `tradeoffs` | `list[str]` | |

## LLM Profile

`reasoning`, via `generate_structured` (model must also have `structured_output`).

## Allowed Tools

None (`allowed_tools=()`). Repository facts are supplied as input by the deterministic
`analyze_repository` node — the agent does not scan the repository itself.

## Prompt Location

- [`prompts/architecture/system.md`](../../prompts/architecture/system.md)
- [`prompts/architecture/task.md`](../../prompts/architecture/task.md) — placeholders `$project_name`, `$requirements`, `$repository_analysis`, `$impact_analysis`, `$previous_architecture`, `$revision_feedback`.

`system.md` is version `1.0.0`, `task.md` version `1.1.0` (adds the impact-analysis
section); each is recorded as `<version>+<sha8>` in execution history.

## Workflow Stage

Node `design_architecture`, lifecycle `ARCHITECTURE_ANALYSIS`, quality-gate / artifact key `architecture`.

```text
requirement_approval (approved) → analyze_repository → design_architecture
  → WAITING_FOR_ARCHITECTURE_APPROVAL → architecture_approval ─┬─ rejected → design_architecture
                                                               └─ approved → select_technology
```

See [execution flow](../workflows/software_factory_execution_flow.md).

## Preconditions

Entry criteria for `architecture` ([`domain/quality_gates.py`](../../src/software_factory/domain/quality_gates.py)):

- "requirements are approved" — `approvals.requirements.status == "approved"`.
- "requirements artifact exists" — `state.requirements` is non-empty.

Also `run` fails if `requirements` is missing or empty (`None`, `""`, `[]` or `{}`).

## Postconditions

- `state.architecture` holds the validated proposal; `artifact_revisions["architecture"]` incremented; artifacts written.
- Lifecycle `WAITING_FOR_ARCHITECTURE_APPROVAL`; `failure` cleared; warnings recorded.
- `AGENT` execution record appended (provider, model, prompt versions, LLM attempts).

## Approval Requirements

Gate `architecture_approval` (approval stage `architecture`, artifact `architecture.md`):

- Approve → `TECHNOLOGY_SELECTION` (the gate re-checks the technology stage's entry criteria before advancing).
- Reject with `--reason` → `revision_feedback["architecture"]` set, back to `ARCHITECTURE_ANALYSIS`; the agent re-runs with the feedback and `previous_architecture`.

## Failure Conditions

Any `FactoryError` (`QualityGateError`, missing input `AgentOutputError`, prompt
`ConfigurationError`, `LLMError`) moves the project to `FAILED`, awaiting `factory retry`.

Schema-level: an empty `components` list is rejected by the schema itself, which the
router treats as invalid structured output (repair → fallback → `LLMError`).

Deterministic `validate_output` rules — **warnings only**, this agent never raises:

| Rule | Result |
|---|---|
| A component references a requirement id not in the approved requirements | Warning |
| A functional requirement is not mapped to any component | Warning |

## Retry Behavior

Same as all design agents (see [Requirement Agent → Retry Behavior](requirement_agent.md#retry-behavior)):
per-model retries on retryable errors (`1 + limits.max_agent_retries` attempts, linear
backoff), up to `limits.max_structured_output_repairs` repair calls for schema-invalid
output, then capability-compatible fallback models; workflow-level `factory retry` after `FAILED`.

## Dependencies

- `LLMClient`, `PromptLibrary`; built by `build_design_agents`.
- Upstream: approved output of the [Requirement Agent](requirement_agent.md); optional repository facts from the [repository analysis tool](../tools/repository_analysis_tool.md) and the impact analysis from the [Impact Analysis Agent](impact_analysis_agent.md) (existing projects).
- Downstream: [Technology Selection Agent](technology_selection_agent.md), [Implementation Planning Agent](implementation_planning_agent.md).

## Examples

From `tests/support/fixtures.py`:

```json
{
  "style": "modular monolith",
  "overview": "A web client talking to a single backend service with a relational store.",
  "components": [
    {"name": "Web client", "responsibility": "UI", "requirement_ids": ["FR-1", "FR-2"]},
    {"name": "Backend API", "responsibility": "Business logic and auth",
     "requirement_ids": ["FR-1", "FR-2", "NFR-1"]}
  ],
  "risks": ["Single deployment unit"],
  "alternatives": [
    {"name": "Microservices", "summary": "Split per domain",
     "reason_not_chosen": "Too complex for the scale"}
  ]
}
```

## Known Limitations

- Coverage and traceability problems are warnings; a proposal that covers no functional requirement can still reach the approval gate (the human reviewer is the check).
- "Do not force a stack" is a prompt rule only; nothing detects product names in the output.
- Non-functional requirements are not checked for coverage (only functional ones).
