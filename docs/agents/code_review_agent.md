# Code Review Agent

**Status:** Implemented (build phase) · **Code:** [`src/software_factory/agents/code_review.py`](../../src/software_factory/agents/code_review.py) · **Spec:** §16, §41, §68.13, §73.2

## Agent Name

`code-review-agent` (version `1.0.0`), class `CodeReviewAgent`.

## Purpose

Review the whole implementation against the approved requirements, architecture and
technology, after every task has been built, tested and committed. It reports defects,
security concerns, missing tests and deviations, each with a severity. Build and tests
have already passed, so the review focuses on what they miss.

## Responsibilities

- Report findings with a stable id (`R-1`, ...), a severity and a category.
- Use these severities:
  - `critical`: data loss, a security hole, or a core requirement not implemented;
  - `high`: a requirement only partly implemented, a real bug, a missing authorization check, or a deviation from approved decisions;
  - `medium`: missing tests for important behavior, poor error handling;
  - `low`: style and maintainability.
- Use these categories: `correctness`, `requirements`, `architecture`, `security`, `tests`, `maintainability`.
- Assess every requirement id as `implemented`, `partial` or `missing`, with evidence.
- Report only problems it can point to in the code shown. Do not invent requirements.

## Inputs

| Key | Required | Source | Notes |
|---|---|---|---|
| `requirements` | yes | `requirements` | |
| `architecture` | yes | `architecture` | |
| `technology_selection` | yes | `technology_selection` | |
| `changes` | yes | git | `git diff <base_commit>..HEAD` (or `git show HEAD` if there is no base), cut to `limits.max_context_chars` (default 40,000) with `… [diff truncated]`. `"No changes."` if empty. |
| `implementation_plan` | no | `implementation_plan` | |
| `file_tree` | no | `list_source_files(source)` | |
| `test_results` | no | `last_verification` | Summary: phase, PASSED/FAILED, each command with its exit code, and the last 1,500 characters of output. |
| `previous_review` | no | `review.report` | Used when re-reviewing after fixes. |

`base_commit` is HEAD at the moment development started, so the diff is everything the
factory changed. For an existing repository, that is only the factory branch's changes.

## Input Schema

`AgentContext` with `stage="code_review"`; see [`agents/base.py`](../../src/software_factory/agents/base.py).

## Output

A `ReviewReport`, written as `review.json` / `review.md` plus
`artifacts/review.rev<N>.json` (`artifact_revisions["code_review"]` counts review runs).
State `review` holds:

- `report`;
- `passed` (true when there are no blocking findings);
- `blocking_findings` (the ids);
- `round`.

## Output Schema

`ReviewReport` ([`domain/development.py`](../../src/software_factory/domain/development.py)):

| Field | Type | Notes |
|---|---|---|
| `summary` | `str` | Required. |
| `findings` | `list[ReviewFinding]` | `id`, `severity`, `category` and `description` are required; `file` and `suggestion` are optional. |
| `requirement_coverage` | `list[RequirementCoverage]` | `requirement_id`, `status` (`implemented` / `partial` / `missing`, free text), `evidence`. |

Blocking severities (`BLOCKING_SEVERITIES`) are `critical` and `high`.

## LLM Profile

`coding` (shipped config: `local_coder`, `qwen2.5-coder:14b`, with fallback `local_model`, `qwen2.5:14b`; temperature 0.2), called through `generate_structured`.

## Allowed Tools

None (`allowed_tools=()`). The workflow collects the diff and the file tree with the
[git tool](../tools/git_tool.md) and passes them in as inputs.

## Prompt Location

- [`prompts/code_review/system.md`](../../prompts/code_review/system.md) (1.0.0)
- [`prompts/code_review/task.md`](../../prompts/code_review/task.md) (1.0.0), with placeholders `$project_name`, `$requirements`, `$architecture`, `$technology_selection`, `$implementation_plan`, `$test_results`, `$file_tree`, `$changes`, `$previous_review`.

## Workflow Stage

Node `code_review` in [`orchestration/nodes/quality_nodes.py`](../../src/software_factory/orchestration/nodes/quality_nodes.py), lifecycle `CODE_REVIEW`. The fix loop it starts (`debug` → `build` → `test`) runs in [`development_nodes.py`](../../src/software_factory/orchestration/nodes/development_nodes.py).

```text
test (last task committed) → code_review ─┬─ blocking & review_rounds < max_review_rounds
                                          │     → BUG_FIXING → debug (Coding Agent, fix) → build → test
                                          │     → commit "fix(review): …" → code_review
                                          └─ otherwise → security_validation
```

**When the round limit is reached** and blocking findings are still open:

- the workflow **continues** to security validation;
- it adds the warning "Code review round limit (N) reached with M blocking finding(s) open ...";
- final validation repeats this as a warning;
- the findings stay visible in `review.md` and `validation.md` for the release decision.

A rejected release resets `review_rounds`, and the workflow comes back here after the fix.

## Preconditions

- Entry criterion `code_review`: "every planned task is implemented" (`development.task_queue` is empty).
- All required inputs are non-empty.

## Postconditions

- The `review` state and artifacts are written, and an `AGENT` record is appended.
- **Blocking findings and rounds left:**
  - `review_rounds` goes up by 1;
  - `phase = "review"` and `debug_iterations = 0`;
  - `fix_context = {source: "review", findings: [blocking findings]}`;
  - lifecycle `BUG_FIXING`.
- **Otherwise:** `code_review` is added to `completed_stages` and the lifecycle moves to `SECURITY_VALIDATION`.

## Approval Requirements

None directly. Its report is one of the documents the human reads at the
`release_approval` gate (`validation.md`, `review.md`, `security.md`).

## Failure Conditions

| Cause | Result |
|---|---|
| Entry criterion unmet (`QualityGateError`), missing input, or LLM failure | `FAILED` |
| `validate_output`: duplicate finding ids | **Fail** (`AgentOutputError`) |
| `validate_output`: an FR/NFR id has no `requirement_coverage` entry | Warning ("Review did not assess requirements [...]") |

## Retry Behavior

- LLM layer: as in [Requirement Agent → Retry Behavior](requirement_agent.md#retry-behavior).
- Fix rounds are bounded by `limits.max_review_rounds` (default 2). Each round's fix is bounded by `limits.max_debug_iterations`.
- `factory retry` runs the node again, after resetting `review_rounds` (and the other fix budgets) and committing any manual edits in `source/` (`chore: manual changes before retry`).

## Dependencies

- `LLMClient` and `PromptLibrary`; the [git tool](../tools/git_tool.md) and [source workspace helpers](../tools/source_workspace_tool.md).
- Upstream: all design artifacts, and the [Coding Agent](coding_agent.md)'s output via git.
- Downstream: [security validation](security_validation_agent.md) (which lists `security` findings), final validation, and the release gate.

## Examples

A blocking review (from `tests/support/fixtures.py`):

```json
{
  "summary": "Looks good.",
  "findings": [
    {"id": "R-1", "severity": "high", "category": "correctness", "file": "calc.py",
     "description": "No docstrings on public API", "suggestion": "Add docstrings"}
  ],
  "requirement_coverage": [
    {"requirement_id": "FR-1", "status": "implemented"},
    {"requirement_id": "FR-2", "status": "implemented"},
    {"requirement_id": "NFR-1", "status": "implemented"}
  ]
}
```

`R-1` is sent to the Coding Agent as `fix_details`. Once the fix passes the tests it is
committed as `fix(review): …`, and the second review is written as revision 2.

## Known Limitations

- Large diffs are cut at `max_context_chars`, so the end of the change set may not be reviewed.
- Severity is the model's judgement. The workflow only acts on it (critical and high block).
- After the round limit, blocking findings no longer stop the workflow. The human release decision is the backstop.
- `requirement_coverage.status` is free text and does not feed traceability, which uses plan tasks and commits.
