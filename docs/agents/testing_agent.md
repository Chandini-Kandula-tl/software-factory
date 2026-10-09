# Testing Agent

**Status:** Implemented (build phase) · **Code:** [`src/software_factory/agents/testing.py`](../../src/software_factory/agents/testing.py) · **Spec:** §15, §40, §41, §68.12, §73.2

## Agent Name

`testing-agent` (version `1.0.0`), class `TestingAgent`.

## Purpose

Explain why a **real** build or test run failed. It classifies the cause, finds the root
cause and proposes a concrete fix for the [Coding Agent](coding_agent.md). It never
decides whether tests passed: that comes only from the command's exit code
(spec §15, §68.12). Its classification lets the workflow stop for a human when the
problem is the environment rather than the code.

## Responsibilities

- Base the analysis only on the actual command output and the code shown. Never claim that anything passed.
- Classify the failure as `code_bug`, `test_bug`, `build_configuration`, `missing_dependency`, `environment` or `unknown`.
- Before blaming a test, check whether the code under test does what the test and requirements expect (including data that changes type when saved and loaded, e.g. JSON object keys come back as strings, and state shared between tests).
- When the output says a previous fix made no progress, look for a different root cause.
- Take the human's guidance (from `factory retry --hint`) into account.
- Give the root cause in one or two sentences and a minimal fix that names the files to change.

Running the tests is **not** this agent's job. The workflow runs the plan's `test`
command through the shell tool (see the [test tool](../tools/test_tool.md)).

## Inputs

| Key | Required | Source | Notes |
|---|---|---|---|
| `verification` | yes | `_fix_details` | Text with the failed step, command, cwd, exit code (and "timed out" if so) and the last 4,000 characters of output. When the same failure has come back, a `NO PROGRESS` note is appended ([stuck detection](../workflows/software_factory_execution_flow.md)). |
| `task` | no | current task | The `ImplementationTask`, or the `{"id": "FIX", ...}` stub outside task work. |
| `acceptance_criteria` | no | `requirements.acceptance_criteria` | |
| `commands` | no | plan `commands` | Non-empty steps only. |
| `relevant_files` | no | `_code_context` | Files named in the output first, then the files changed last, then the task's `likely_files`, within `limits.max_context_chars`. |
| `human_guidance` | no | `development.human_hint` | The hint from `factory retry <project> --hint "…"`, kept until the failing work passes. |

Plus `project_name`.

## Input Schema

`AgentContext` with `stage="bug_fixing"`; see [`agents/base.py`](../../src/software_factory/agents/base.py).
The failed run is a `CommandRun` inside a `VerificationResult`
([`domain/development.py`](../../src/software_factory/domain/development.py)), rendered to text.

## Output

A `TestFailureAnalysis`. It is written as `test-analysis.json` / `test-analysis.md`
(latest version only), passed to the Coding Agent as `failure_analysis`, and stored in
state `test_analysis` once the fix is applied.

## Output Schema

`TestFailureAnalysis` ([`domain/development.py`](../../src/software_factory/domain/development.py)):

| Field | Type | Notes |
|---|---|---|
| `summary` | `str` | Required. |
| `failure_category` | `FailureCategory` | Default `unknown`. |
| `root_cause` | `str` | Required. |
| `suggested_fix` | `str` | Required. |
| `files_to_change` | `list[str]` | |
| `failing_tests` | `list[str]` | |

## LLM Profile

`coding` (shipped config: `local_coder`, `qwen2.5-coder:14b`, with fallback `local_model`, `qwen2.5:14b`; temperature 0.2), called through `generate_structured`.

## Allowed Tools

None (`allowed_tools=()`).

## Prompt Location

- [`prompts/testing/system.md`](../../prompts/testing/system.md) (1.2.0): compare both the code and the test with the requirements and classify code_bug vs test_bug; look for a different root cause after a NO PROGRESS note.
- [`prompts/testing/task.md`](../../prompts/testing/task.md) (1.1.0), with placeholders `$project_name`, `$verification`, `$human_guidance`, `$task`, `$acceptance_criteria`, `$commands`, `$relevant_files`.

## Workflow Stage

It runs at the start of the `debug` node (lifecycle `BUG_FIXING`,
[`development_nodes.py`](../../src/software_factory/orchestration/nodes/development_nodes.py)),
**only** when the fix source is `verification`: a failed setup, format, lint, build or
test run in the `build` or `test` node ([`development_nodes.py`](../../src/software_factory/orchestration/nodes/development_nodes.py))
or the `final_validation` node ([`quality_nodes.py`](../../src/software_factory/orchestration/nodes/quality_nodes.py)).
This includes the `test` node's planned-test-files check: when a task's planned test
files are missing, the verification holds one synthetic run with
`argv = ["(factory check) planned test files exist"]`, exit code 1 and the output "The
task's planned tests were not written. Missing test files: …", and this agent analyses
it like any other failure. Fixes for review findings, security findings, validation
findings and release feedback skip it.

```text
build / test / final_validation ─ command failed → BUG_FIXING → debug
  debug: stuck check ─ same failure 3 times → FAILED ("No progress: …")
         Testing Agent ─┬─ category environment → FAILED (no fix attempt)
                        └─ otherwise → Coding Agent (mode=fix, failure_analysis,
                                       up to limits.fix_candidates candidates) → BUILDING
```

## Preconditions

- `development.fix_context` holds a `VerificationResult` with `passed=false`.
- `debug_iterations < limits.max_debug_iterations` (checked before the workflow enters `BUG_FIXING`).
- `verification` is non-empty.

## Postconditions

- `test-analysis.md` / `test-analysis.json` are written and an `AGENT` execution record is appended.
- Category `environment`: lifecycle `FAILED` with the message "Environment problem, not a code problem: `<root_cause>`" and the hint "`<suggested_fix>` Then `factory retry`." (A retry re-runs build and tests from `BUILDING`.)
- Any other category: the Coding Agent runs in the same node.

## Approval Requirements

None. An `environment` failure waits for a human at the standard failure gate
(`factory retry` or `factory cancel`).

## Failure Conditions

| Cause | Result |
|---|---|
| Required input missing, or LLM failure | `FAILED` |
| `failure_category == environment` | `FAILED`, without calling the Coding Agent |

There is no custom `validate_output`; the base class returns no warnings.

## Retry Behavior

- LLM layer: as in [Requirement Agent → Retry Behavior](requirement_agent.md#retry-behavior).
- Each `debug` run counts as one fix attempt (`debug_iterations`), even when the Testing Agent itself fails.
- Before this agent is called, `debug` compares the failure's signature with earlier ones; the third identical failure in a row stops the project with "No progress: …" without calling it.
- `factory retry` after a failure in `debug` (for example an `environment` classification, once the human has installed the missing program) resets `debug_iterations`, `review_rounds` and `security_rounds` to 0 and clears the failure history; `--hint "…"` gives this agent `human_guidance`. It resumes at **`BUILDING`**, not `debug`: build and tests run again, and this agent is called only if they still fail, with fresh output. See [Coding Agent → Retry Behavior](coding_agent.md#retry-behavior).

## Dependencies

- `LLMClient` and `PromptLibrary`; [source workspace helpers](../tools/source_workspace_tool.md) for file selection.
- Downstream: the [Coding Agent](coding_agent.md).

## Examples

Analysis of a buggy `add` (from `tests/support/fixtures.py`):

```json
{
  "summary": "add is wrong",
  "failure_category": "code_bug",
  "root_cause": "add subtracts",
  "suggested_fix": "return a + b",
  "files_to_change": ["calc.py"]
}
```

The same analysis with `"failure_category": "environment"` stops the project at
`FAILED` without a fix attempt (`test_environment_failures_stop_for_a_human`).

## Known Limitations

- The classification is the model's judgement. In the first live build (2026-09-30) `qwen2.5:14b` blamed correct tests for implementation bugs (JSON keys loaded as strings, id reuse after delete); prompt 1.1.0 addressed this, but then over-corrected: on task T-4 the test itself was wrong (it expected integer keys in raw JSON) and the agents kept changing correct code. Prompt 1.2.0 asks for an explicit code-vs-test judgement against the requirements; a small local model may still need a human hint.
- The analysis is still only a suggestion. A wrong `environment` label stops the workflow early; a missed one wastes fix attempts.
- It sees only the last 4,000 characters of output. Full logs are in `artifacts/tool-runs/`.
- There is no test-strategy, coverage or regression reporting yet (spec §15). Regressions are caught by re-running the whole `test` command after every change and again in final validation.
