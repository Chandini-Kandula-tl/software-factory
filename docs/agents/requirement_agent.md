# Requirement Agent

**Status:** Implemented (design phase) · **Code:** [`src/software_factory/agents/requirement.py`](../../src/software_factory/agents/requirement.py) · **Spec:** §10.1, §68.9, §73.2

## Agent Name

`requirement-agent` (version `1.0.0`), class `RequirementAgent`.

## Purpose

Analyze the user's requirements, detect ambiguity, and produce structured, traceable
requirements with acceptance criteria. When important information is missing it
**proposes** clarification questions. It never decides whether the workflow waits for
the user — that decision is deterministic workflow code (see
[Approval Requirements](#approval-requirements) and [Workflow Stage](#workflow-stage)).

## Responsibilities

- Identify functional requirements (`FR-1`, `FR-2`, ...) and non-functional requirements (`NFR-1`, ...), keeping ids stable across revisions.
- Identify constraints and explicit assumptions (low-impact gaps only).
- Write acceptance criteria (`AC-1`, ...) that reference the requirement ids they verify.
- Detect missing high-impact information and propose clarification questions, each with a reason, an impact area and a `required` flag.
- Not re-ask questions already answered in earlier clarification rounds.
- Not choose technologies (a later stage).

These rules live in the system prompt; the deterministic checks in
[Failure Conditions](#failure-conditions) enforce the parts that can be checked.

## Inputs

The agent receives an `AgentContext` built by `LLMAgent.build_context`, which copies
**only** the agent's declared input keys (and drops `None` values) from what the
workflow node offers. It never sees the whole project state.

| Key | Required | Source in workflow state | Notes |
|---|---|---|---|
| `requirements_input` | yes | `requirements_input` | Raw requirement text given via `factory create --requirements/-f` or `factory requirements`. |
| `project_mode` | no | `project_mode` | `greenfield` or `existing`. |
| `clarifications` | no | `clarifications` | Answers from earlier rounds (`ClarificationAnswer` list). |
| `previous_requirements` | no | `requirements` | The previous output, passed when re-running (after clarification or rejection). |

Plus `project_name` and `revision_feedback` (the reviewer's rejection reason for the
`requirements` stage, or `None`).

## Input Schema

`AgentContext` ([`agents/base.py`](../../src/software_factory/agents/base.py)):

```text
project_id: str
project_name: str
stage: str                    # "requirements"
inputs: dict[str, Any]        # only the keys in the table above
revision_feedback: str | None
```

`clarifications` items follow `ClarificationAnswer`
([`domain/requirements.py`](../../src/software_factory/domain/requirements.py)):
`question_id`, `question`, `answer`, `round`, `answered_at`.

Non-string inputs are rendered into the task prompt as indented JSON; missing optional
inputs render as `None.`

## Output

Structured `RequirementAnalysis`. After the workflow node has applied the clarification
decision, the recorder writes, under `<workspace>/projects/<project_id>/`:

| File | Content |
|---|---|
| `requirements.json` | The `RequirementAnalysis` (machine-readable). |
| `requirements.md` | Human-readable render, including answered clarifications and the revision number. |
| `artifacts/requirements.rev<N>.json` | Copy of revision `N` (one per agent run of this stage). |
| `clarification-questions.md` | Pending questions with their factory-assigned ids (when waiting). |

## Output Schema

`RequirementAnalysis` ([`domain/requirements.py`](../../src/software_factory/domain/requirements.py)):

| Field | Type | Notes |
|---|---|---|
| `summary` | `str` | Required. |
| `application_type` | `str` | Default `""`. |
| `functional_requirements` | `list[RequirementItem]` | `id`, `description`, `priority` (`must`/`should`/`could`, default `must`), `source` (`user`/`clarification`/`assumption`, default `user`). |
| `non_functional_requirements` | `list[RequirementItem]` | Same shape. |
| `constraints` | `list[str]` | |
| `assumptions` | `list[str]` | |
| `open_questions` | `list[str]` | The factory appends leftover questions here (see below). |
| `acceptance_criteria` | `list[AcceptanceCriterion]` | `id`, `requirement_ids`, `description`. |
| `needs_clarification` | `bool` | Advisory only; the workflow does not use it to decide. |
| `clarification_questions` | `list[ProposedQuestion]` | `question`, `reason`, `impact_area` (`ImpactArea` enum), `required` (default `true`). **No id** — ids are assigned by the factory. |

`ImpactArea` values: `functionality`, `architecture`, `technology`, `security`, `data`,
`integrations`, `deployment`, `testing`, `non_functional`, `other`.

## LLM Profile

`reasoning`. The request is sent through `LLMClient.generate_structured`, so the chosen
model must also declare the `structured_output` capability in addition to the profile's
`required_capabilities`. Temperature comes from the profile. See
[LLM architecture](../architecture/llm_architecture.md).

## Allowed Tools

None (`allowed_tools=()`). The agent cannot touch the file system, git or a shell.
Enforced by a unit test (`tests/unit/test_agents.py`).

## Prompt Location

- [`prompts/requirement/system.md`](../../prompts/requirement/system.md)
- [`prompts/requirement/task.md`](../../prompts/requirement/task.md) — placeholders `$project_name`, `$project_mode`, `$requirements_input`, `$clarifications`, `$previous_requirements`, `$revision_feedback`.

Each file has front matter with a version (`system.md` 1.0.0, `task.md` 1.1.0). Execution history records each prompt as
`<version>+<sha8>` (first 8 hex chars of the SHA-256 of the body), so an edited prompt is
traceable even without a version bump. Placeholders use `string.Template.safe_substitute`.

## Workflow Stage

Node `analyze_requirements` ([`orchestration/nodes/agent_nodes.py`](../../src/software_factory/orchestration/nodes/agent_nodes.py)),
lifecycle status `REQUIREMENTS_ANALYSIS`, quality-gate / artifact key `requirements`.

```text
load_project → analyze_requirements ─┬─ required question & rounds left → WAITING_FOR_REQUIREMENT_CLARIFICATION → wait_for_user_input → analyze_requirements
                                     └─ otherwise → WAITING_FOR_REQUIREMENT_APPROVAL → requirement_approval
```

**Deterministic clarification rule** (`_clarification_decision`):

0. First, drop any proposed question that repeats one the user already answered
   (normalized text similarity ≥ 0.85, `difflib`). Small local models sometimes
   re-ask despite the prompt; this stops the user being asked the same thing
   twice. A warning records how many were dropped.
1. Wait for the user **only if** at least one proposed question has `required: true`
   **and** `clarification_round < limits.max_clarification_rounds` (default 5).
2. When waiting, **all** proposed questions (required and optional) get factory ids
   `Q<n>` from `question_counter`, which continues across rounds (round 1 may yield
   `Q1..Q3`, round 2 starts at `Q4`). `clarification_round` is incremented and stored on each question.
3. Otherwise the proposed questions are appended to `open_questions` so the human
   reviewer sees them instead of silent guesses. If required questions were left because
   the round limit was reached, a warning is recorded.

`wait_for_user_input` resumes only when every *required* question has a non-empty
answer (`factory answer <project> <id> "..."`, then `factory continue <project>`);
otherwise it stays at the same gate. Answers are appended to `clarifications` and the
status goes back to `REQUIREMENTS_ANALYSIS`.

See [execution flow](../workflows/software_factory_execution_flow.md) and
[orchestration architecture](../architecture/orchestration_architecture.md).

## Preconditions

Quality-gate entry criteria for `requirements`
([`domain/quality_gates.py`](../../src/software_factory/domain/quality_gates.py)):

- "user requirements are available" — `requirements_input` is non-empty after stripping.

In addition, `LLMAgent.run` fails if the required input `requirements_input` is missing,
`""`, `[]` or `{}`.

## Postconditions

On success:

- `state.requirements` holds the validated output (with leftover questions merged into `open_questions` when not waiting).
- `artifact_revisions["requirements"]` is incremented; artifacts above are written.
- Lifecycle is `WAITING_FOR_REQUIREMENT_CLARIFICATION` (with `pending_questions`) or `WAITING_FOR_REQUIREMENT_APPROVAL`.
- `failure` is cleared; agent warnings are appended to `state.warnings` prefixed with the agent name.
- An `AGENT` execution record (provider, model, profile, prompt versions, every LLM attempt) is appended to `execution-history.json`.

## Approval Requirements

Gate `requirement_approval` (approval stage `requirements`, artifact `requirements.md`):

- `factory approve <project>` → records an `ApprovalDecision`, marks the stage completed, moves to `REPOSITORY_ANALYSIS` (only if the next stage's entry criteria are met).
- `factory reject <project> --reason "..."` → stores the reason as `revision_feedback["requirements"]` and returns to `REQUIREMENTS_ANALYSIS`. The agent re-runs with that feedback **and** its previous output (`previous_requirements`).

Approval decisions are persisted in `decisions.md` / `decision_log` with the artifact revision they apply to.

## Failure Conditions

Any `FactoryError` from the stage moves the project to `FAILED` (recording stage,
error kind, redacted message, hint and `resume_status`), and it waits for
`factory retry <project>` (re-runs this node) or `factory cancel`.

| Cause | Error |
|---|---|
| Entry criterion unmet | `QualityGateError` |
| Required input missing | `AgentOutputError` |
| Prompt file missing | `ConfigurationError` |
| LLM failure after retries/fallbacks/repairs | `LLMError` |
| Deterministic `validate_output` failure (below) | `AgentOutputError` |

Deterministic `validate_output` rules:

| Rule | Result |
|---|---|
| Duplicate ids across functional + non-functional requirements | **Fail** |
| No functional requirements **and** no clarification questions | **Fail** |
| Acceptance criterion references an unknown requirement id | Warning |
| `needs_clarification` is true but no questions proposed | Warning |

## Retry Behavior

- **LLM layer** ([`llm/router.py`](../../src/software_factory/llm/router.py)): each model gets `1 + limits.max_agent_retries` attempts on retryable errors (`timeout`, `rate_limit`, `provider_unavailable`, `network`, `malformed_response`) with linear backoff `retry_backoff_seconds × attempt`. Schema-invalid output triggers up to `limits.max_structured_output_repairs` repair calls on the same model (the validation error is fed back). If a model still fails with a fallback-eligible kind (retryable kinds plus `model_unavailable`, `authentication`, `context_length_exceeded`, `invalid_structured_output`), the next capability-compatible fallback model in the profile is tried.
- **Agent layer:** `validate_output` failures are **not** retried automatically — the stage goes to `FAILED`.
- **Workflow layer:** `factory retry` re-runs the failed node from the checkpoint; the revision counter is not advanced by failed runs.

## Dependencies

- `LLMClient` (the `ModelRouter`) and `PromptLibrary`, injected via `build_design_agents` in [`agents/catalog.py`](../../src/software_factory/agents/catalog.py).
- Domain schemas in `domain/requirements.py`.
- Downstream consumers: [Architecture Agent](architecture_agent.md), [Technology Selection Agent](technology_selection_agent.md), [Implementation Planning Agent](implementation_planning_agent.md) (all receive the approved `requirements`).

## Examples

Proposed output needing clarification (from `tests/support/fixtures.py`):

```json
{
  "summary": "Employee management application administered by admins.",
  "application_type": "unknown",
  "functional_requirements": [
    {"id": "FR-1", "description": "Administrators can create, update and delete employees."}
  ],
  "needs_clarification": true,
  "clarification_questions": [
    {"question": "Should this be a web application, mobile application, or both?",
     "reason": "Determines the client architecture", "impact_area": "architecture", "required": true},
    {"question": "Who should be able to access employee information?",
     "reason": "Determines the authorization model", "impact_area": "security", "required": true},
    {"question": "Do you have a preferred color scheme?", "impact_area": "other", "required": false}
  ]
}
```

In round 1 the factory turns these into `Q1`, `Q2`, `Q3` (round 1) and waits. After
answers, a clear analysis might be:

```json
{
  "summary": "Web application where administrators manage employee records.",
  "application_type": "web application",
  "functional_requirements": [
    {"id": "FR-1", "description": "Administrators can create, update and delete employees."},
    {"id": "FR-2", "description": "Employees can view their own profile.", "source": "clarification"}
  ],
  "non_functional_requirements": [
    {"id": "NFR-1", "description": "Only authenticated users can access data."}
  ],
  "assumptions": ["Up to 500 employees"],
  "acceptance_criteria": [
    {"id": "AC-1", "requirement_ids": ["FR-1"], "description": "Admin can add an employee."}
  ],
  "needs_clarification": false,
  "clarification_questions": []
}
```

## Known Limitations

- Id stability across revisions (`FR-x` stays `FR-x`) is requested in the prompt, not enforced.
- Only uniqueness of requirement ids is checked; acceptance-criterion ids are not checked for duplicates, and dangling references are warnings, not failures.
- `needs_clarification` is advisory; only the `required` flags drive the wait decision.
- When waiting for clarification, optional questions are also shown and get ids; they do not block resumption.
- `clarification_round` is not reset after a rejection, so a rejected-then-revised analysis has fewer rounds left.
- `artifact_revisions["requirements"]` counts agent runs, so clarification rounds also create new `rev<N>` files.
