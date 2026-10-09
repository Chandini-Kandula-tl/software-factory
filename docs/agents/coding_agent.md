# Coding Agent

**Status:** Implemented (build phase) · **Code:** [`src/software_factory/agents/coding.py`](../../src/software_factory/agents/coding.py) · **Spec:** §14, §36, §40, §41, §68.12, §73.2

## Agent Name

`coding-agent` (version `1.0.0`), class `CodingAgent`.

## Purpose

Implement one approved plan task at a time, or fix a reported problem, by **proposing
complete file changes** that follow the approved architecture and technology. The agent
never writes to disk itself and never decides whether its code works: the workflow
validates and applies the changes, then proves them with the project's real build and
test commands.

## Responsibilities

- Implement the current task (`mode: implement`) or resolve a reported problem (`mode: fix`).
- Follow the approved architecture and technology exactly: no unapproved frameworks, languages, databases or services.
- Return **full** file contents for every created or updated file (no diffs, snippets or `...`), or, for small fixes to existing files, targeted `edits` (`find` copied exactly from the file and occurring exactly once, `replace`). Paths are relative to the repository root and use `/`.
- Write or update automated tests that the approved `test` command can run, returned separately in `test_code` at exactly the paths listed in the task's `tests`.
- Keep dependency manifests (`package.json`, `requirements.txt`, `pyproject.toml`, ...) consistent with imports.
- Never hard-code secrets. Read them from environment variables and document them (e.g. `.env.example`).
- In fix mode, change only what is needed. Tests describe the required behaviour: fix the implementation, and change a test only when the test itself contradicts the requirements.
- Tests must not create or change files inside the repository (use temporary folders or in-memory data).
- Check a short list of common mistakes before answering: JSON-loaded data has string keys, ids stay unique after deletions (`max + 1`, never `count + 1`), every used name is imported, tests are independent of order and earlier runs.
- When an approved decision cannot work, return a **change request** and no files instead of working around it (spec §36).

These rules live in the system prompt. The deterministic checks in
[Failure Conditions](#failure-conditions) enforce the parts that can be checked.

## Inputs

`LLMAgent.build_context` copies only the declared keys (and drops `None` values).

| Key | Required | Source | Notes |
|---|---|---|---|
| `mode` | yes | node | `"implement"` (`implement_task`) or `"fix"` (`debug`). |
| `task` | yes | `implementation_plan.tasks[...]` | The current `ImplementationTask`. For fixes outside task work (review, security, validation, release), a stub `{"id": "FIX", "title": "Fix <source> problems"}`. |
| `requirements` | yes | `requirements` | Approved `RequirementAnalysis`. |
| `architecture` | yes | `architecture` | Approved `ArchitectureProposal`. |
| `technology_selection` | yes | `technology_selection` | Approved `TechnologySelection`. |
| `commands` | no | plan `commands` | Only the non-empty steps of `ProjectCommands`. |
| `file_tree` | no | `list_source_files(source)` | Up to 400 project files. |
| `relevant_files` | no | `_code_context` | `{path: content}` within `limits.max_context_chars` (see the [source workspace tool](../tools/source_workspace_tool.md)). |
| `fix_details` | no | `_fix_details` (fix mode) | One of: the failed command with its exit code and the last 4,000 characters of output; the review, security or validation findings as JSON; or the human's reason for rejecting the release. For build/test failures the workflow appends a `NO PROGRESS` note when the same failure repeats, and "EARLIER FIX ATTEMPTS FOR THIS FAILURE (all failed — do something different)" with the last 4 failed attempts (summary, changed files, result). See [Retry Behavior](#retry-behavior). |
| `failure_analysis` | no | Testing Agent (fix mode) | A `TestFailureAnalysis`, only for failed build/test runs. |
| `change_request_feedback` | no | `development.change_request_feedback` | Set when a human rejected this agent's earlier change request. Used once. |
| `allow_edits` | no | `debug` (fix mode) | `true`, or `false` for the rest of a fix round once one candidate's edit could not be applied (small models repeat a failing edit). With `false`, an answer containing `edits` is rejected by `validate_output`. Not rendered in the prompt; the failed attempt's note ("Next attempt: do NOT use `edits`; return the complete file in `files`.") tells the model. |
| `human_guidance` | no | `development.human_hint` (fix mode) | The hint from `factory retry <project> --hint "…"`. Rendered as "Guidance from the human reviewer (follow it; it overrides the analysis below)". Kept until the failing work passes. |

Plus `project_name`. This agent does not use `revision_feedback`.

**How `relevant_files` is chosen** (`_code_context`): preferred files first, then
dependency manifests, then the rest of the tree, until the character budget runs out.
In implement mode the preferred files are the task's `likely_files` and `tests`. In fix
mode they are the files named in the failure output, then the files changed last time,
then the task's `likely_files`. A small project therefore gets its whole code base.

## Input Schema

`AgentContext` ([`agents/base.py`](../../src/software_factory/agents/base.py)) with
`stage="development"` (implement) or `stage="bug_fixing"` (fix). Payloads follow
`ImplementationTask` / `ProjectCommands` ([`domain/planning.py`](../../src/software_factory/domain/planning.py))
and the design-phase schemas. They are rendered into the task prompt as JSON.

## Output

A `CodeChanges` proposal. The workflow then does one of two things:

1. If `change_request` is set, the [Impact Analysis Agent](impact_analysis_agent.md) runs (mode `change_request`), then the workflow writes `change-request.json` / `change-request.md` and stops at `WAITING_FOR_CHANGE_REQUEST_APPROVAL`. **No files are applied.**
2. Otherwise it applies every `FileChange` in `all_files` (`files` + `test_code`) through the [filesystem tool](../tools/filesystem_tool.md) (`write_text` for create/update, `delete` for delete), then every `FileEdit` in `edits` (the file must exist and `find` must occur exactly once, else `ChangeApplyError`: "Edit for X: the file does not exist" / "Edit for X: the `find` text occurs N times (must be exactly once)"). The tool is obtained with `ToolKit.require(FILESYSTEM, spec.allowed_tools)`, so it is used only because this agent declares it. It records an `apply_changes` `TOOL` execution, stores the paths as `development.last_changes`, and moves to `BUILDING`.

Nothing is committed at this point. Commits happen only after the test command passes.
In task work, the `test` node first checks that the task's planned test files (path-like
entries of `task.tests`) exist; a missing file is a failed verification ("The task's
planned tests were not written. Missing test files: …") that comes back to this agent
in fix mode.

## Output Schema

`CodeChanges` ([`domain/development.py`](../../src/software_factory/domain/development.py)):

| Field | Type | Notes |
|---|---|---|
| `summary` | `str` | Required. In fix mode, its first 120 characters become the fix commit message. |
| `files` | `list[FileChange]` | Source, configuration and manifest files. `path` (relative, `/`), `action` (`create` by default, `update` or `delete`), `content` (full content; empty for delete). |
| `test_code` | `list[FileChange]` | Automated test files with real assertions, at exactly the paths in the task's `tests`. Required whenever the task lists tests. Same `FileChange` shape. |
| `edits` | `list[FileEdit]` | Small targeted changes to existing files (preferred for small fixes): `path`, `find` (exact text, must occur exactly once), `replace`. |
| `notes` | `list[str]` | |
| `change_request` | `ChangeRequest \| None` | `target` (`architecture` / `technology` / `implementation_plan`), `problem`, `proposed_change`, `impact` (default `""`). |

`all_files` (a property, not a schema field) is `files + test_code`. Validation and
application both use it. `touched_paths` (also a property) is every path in `all_files`
and `edits`, without duplicates; fix candidates are snapshotted and rolled back over it.

Why a separate `test_code` field: in live runs `qwen2.5:14b` skipped the tests when they
shared the `files` list. With the separate field it passed 3 of 3 live runs of
`factory evaluate --case code_generation`.

## LLM Profile

`coding` in `config/factory.yaml`: preferred model `local_coder` (`qwen2.5-coder:14b`,
`num_ctx` 32768), fallback `local_model` (`qwen2.5:14b`), `required_capabilities: [coding]`,
temperature 0.2 (a little variety helps when several fix candidates are tried). The agent calls `generate_structured`, so the model also needs the
`structured_output` capability. See [LLM architecture](../architecture/llm_architecture.md).

## Allowed Tools

`(filesystem,)`. The agent does not call tools itself: `LLMAgent` makes one structured
call. The declaration is enforced: `_apply_changes` gets `FilesystemTool` (confined to the
project's source directory) only through `ToolKit.require(FILESYSTEM, allowed_tools)`,
which raises `ToolPermissionError` ("not allowed here") for an undeclared tool. The agent
has no git, shell, build or test access.

## Prompt Location

- [`prompts/coding/system.md`](../../prompts/coding/system.md) (1.5.0: decide code_bug vs test_bug from the requirements)
- [`prompts/coding/task.md`](../../prompts/coding/task.md) (1.2.0), with placeholders `$project_name`, `$mode`, `$task`, `$requirements`, `$architecture`, `$technology_selection`, `$commands`, `$file_tree`, `$relevant_files`, `$fix_details`, `$human_guidance`, `$failure_analysis`, `$change_request_feedback`.

The system prompt asks for source files in `files` and tests in `test_code`, prefers
`edits` for small fixes, says to fix the implementation rather than the tests unless the
test is wrong, forbids tests that write into the repository, and ends with a
common-pitfalls checklist (JSON string keys, unique ids after deletion, imports,
independent tests). Task prompt 1.2.0 adds the human-guidance section. The task prompt
still tells the model that the factory checks the task's test files exist before
running the test command. Execution history records each prompt as `<version>+<sha8>`.

## Workflow Stage

Nodes `implement_task` (lifecycle `DEVELOPMENT`) and `debug` (lifecycle `BUG_FIXING`) in
[`orchestration/nodes/development_nodes.py`](../../src/software_factory/orchestration/nodes/development_nodes.py).

```text
prepare_development → implement_task → build → test ─┬─ pass → commit "T-n: title" → next task / code_review
                                                      └─ fail → debug (Testing Agent → Coding Agent, mode=fix) → build
code_review / security_validation / final_validation / release rejection ─ findings → debug → build → test → back
implement_task / debug ─ change_request → Impact Analysis Agent → change_request_approval
```

A successful fix returns to where it came from (`PHASE_RETURN`):

| Fix phase | Returns to |
|---|---|
| `task` | next task |
| `review`, `release_feedback` | `CODE_REVIEW` |
| `security` | `SECURITY_VALIDATION` |
| `validation` | `FINAL_VALIDATION` |

See the [execution flow](../workflows/software_factory_execution_flow.md).

## Preconditions

- `prepare_development` has checked that:
  - the `development` entry criteria hold (all four design approvals and a plan);
  - the plan's `commands` pass `command_problems` (shell enabled, every cwd inside the repo, no forbidden program, every program allowlisted **and** installed). The same check was shown on the plan before approval.
- `prepare_development` has also prepared the source repository:
  - greenfield: `projects/<id>/source` as its own git repository (`git init` when `source/.git` is missing, then a check that `git rev-parse --show-toplevel` is `source/`), `DEFAULT_GITIGNORE` and an initial commit;
  - existing: a clean repository switched to branch `factory/<project_id>`.
- `LLMAgent.run` fails if `mode`, `task`, `requirements`, `architecture` or `technology_selection` is missing or empty.

## Postconditions

- **Changes applied:** lifecycle `BUILDING` and `development.last_changes` set. `development.fix_context` is cleared in implement mode, or kept with a `summary` in fix mode.
- **Change request:** `state.change_request` holds the request plus `task_id`, `origin_status`, `raised_at`, `summary`, `completed_tasks` (task ids already committed), `changed_files` (`git diff --name-only base_commit..HEAD`) and `impact_analysis` (or `impact_analysis_error` = "Impact analysis failed; see errors.md" if the Impact Analysis Agent failed; the request still reaches the human). Lifecycle `WAITING_FOR_CHANGE_REQUEST_APPROVAL`.
- In both cases an `AGENT` execution record is appended to `execution-history.json`.

## Approval Requirements

Normal changes need no approval: the evidence is the real build/test result, and the
human approves the release at the end. A **change request** is first assessed by
the [Impact Analysis Agent](impact_analysis_agent.md), then stops at the
`change_request_approval` gate (artifact `change-request.md`, with the Coding Agent's
own `impact` text, the impact analysis and the deterministic "What is affected so far"):

- **`factory approve <project> --stage change_request`**
  - Re-opens the target decision and every stage after it:
    - `architecture` re-opens architecture, technology and plan;
    - `technology` re-opens technology and plan;
    - `implementation_plan` re-opens the plan only.
  - Writes the request into that stage's `revision_feedback` and returns to the design stage.
  - Removes the re-opened stages and `development`, `code_review`, `security_validation`, `final_validation` from `completed_stages`.
  - Completed tasks are kept. After the plan is approved again, `prepare_development` rebuilds the task queue.
- **`factory reject <project> --reason "..."`** (a reason is required)
  - Returns to where the request came from (`DEVELOPMENT` or `BUG_FIXING`).
  - The agent runs again with `change_request_feedback` telling it to keep the approved decisions.

See the [change request workflow](../workflows/change_request_workflow.md).

## Failure Conditions

Any `FactoryError` moves the project to `FAILED` (retry with `factory retry`).

| Cause | Result |
|---|---|
| Required input missing | `AgentOutputError` |
| LLM failure after retries, fallbacks and repairs | `LLMError` |
| `validate_output` (over `all_files`): absolute path, `..` in path, or empty path | **Fail** (`AgentOutputError`) |
| `validate_output`: path inside `.git` | **Fail** |
| `validate_output`: same path twice in one response | Warning ("… was given more than once; the last version is used"); earlier copies are dropped |
| `validate_output`: edit path absolute, with `..` or inside `.git`; edit with empty `find` | **Fail** (`AgentOutputError`) |
| `validate_output`: `edits` given while `allow_edits` is `false` | **Fail** (`AgentOutputError`: "`edits` are not allowed in this attempt because an earlier edit could not be applied; …") — the model gets its one correction attempt |
| Create/update with empty content | Warning |
| Change request **and** files in the same response | Warning ("files will not be applied") |
| No files, no edits and no change request | Warning |
| Edit's file missing, or `find` not occurring exactly once | `ChangeApplyError`: in a multi-candidate fix round the candidate is rolled back and the next one is asked; otherwise `FAILED` |
| A fix round ends with no candidate kept (the earlier candidates failed and were rolled back, and the last one could not be applied or the agent call failed) | `FAILED`: "None of the N proposed fixes could be applied" when none could be applied, else "None of the N proposed fixes worked (X were tested and still failed, Y could not be applied)" |
| Planned test files missing after the change | Failed verification → fix loop (see [test tool](../tools/test_tool.md#planned-test-files-must-exist)) |
| Applying fails (`ToolPermissionError`, e.g. a symlink escape, a file over 2 MB, or `filesystem` not in `allowed_tools`; or `OSError`) | `FAILED` |

## Retry Behavior

- **LLM layer:** router retries, structured-output repairs and profile fallbacks, as in [Requirement Agent → Retry Behavior](requirement_agent.md#retry-behavior).
- **Validation repair:** when `validate_output` raises `AgentOutputError` (e.g. a path outside the repo), `LLMAgent.run` sends the model its answer back with "Your answer failed the factory's checks: <reason>. Return the corrected JSON object only." (`validation_repairs` = 1 attempt). A second failure fails the stage.
- **Fix candidates (build/test failures):** in one `debug` round the agent is asked up to `limits.fix_candidates` times (default 3). Each candidate is applied and checked with the real commands (`_check_steps`: setup if manifests changed, format, lint, build, test, plus the planned-test-file check). The first that passes is kept (history: "Fix candidate k of N passes; keeping it"). A failing candidate is rolled back (`_snapshot` / `_restore` over `touched_paths`) and described to the next one; after a candidate whose edit could not be applied, the rest of the round must send complete files (`allow_edits: false`); the last candidate is kept even if it fails, so the normal build → test loop continues from it. Review, security, validation and release-feedback fixes use one candidate.
- **Stuck detection:** each build/test failure gets a signature (its error lines, digits ignored). A repeat adds a `NO PROGRESS` note to `fix_details`; after 3 identical failures (`NO_PROGRESS_LIMIT`) the project fails with "No progress: the same failure came back 3 times in a row after automatic fixes" and a hint to retry with `--hint`.
- **Failed-attempt memory:** the fix chosen in a round is remembered (`development.pending_attempt`); if the failure comes back it moves to `development.fix_attempts` with its result, together with the rolled-back candidates. They are shown as "EARLIER FIX ATTEMPTS FOR THIS FAILURE". A passing test clears the memory, the failure history and the human hint.
- **Fix loop (build/test failures):**
  - A failed build or test goes to `BUG_FIXING` while `development.debug_iterations < limits.max_debug_iterations` (default 5).
  - `debug` increments the counter; a passing test resets it to 0.
  - When the budget is used up the project goes to `FAILED` ("`<step>` still failing after N fix attempt(s)").
- **Review and security fixes:** each is bounded by `limits.max_review_rounds` (default 2).
- **Human retry:** `factory retry [--hint "…"]` calls `prepare_retry` ([`development_nodes.py`](../../src/software_factory/orchestration/nodes/development_nodes.py)), which does three things:
  - Resets `debug_iterations`, `review_rounds`, `security_rounds` and the failure history to 0/empty, giving fresh fix budgets. A `--hint` is stored as `development.human_hint` and given to this agent (and the Testing Agent) as `human_guidance` until the failing work passes.
  - Changes where the workflow resumes when the failure happened during a fix of a build/test failure (fix source `verification`, e.g. an `environment` classification or a failed fix step). The workflow resumes at `BUILDING` instead of `BUG_FIXING` and clears the stale `fix_context`. Build and tests therefore run again and the old output is not re-analysed.
  - Commits any manual edits in `source/` as `chore: manual changes before retry` once all tasks are done (empty task queue and a dirty tree), so review, security and final validation see a clean tree, and updates `development.last_commit`.

  Otherwise the failed node runs again.

## Dependencies

- `LLMClient` and `PromptLibrary`, built by `build_agents` in [`agents/catalog.py`](../../src/software_factory/agents/catalog.py).
- [Filesystem tool](../tools/filesystem_tool.md) and [source workspace helpers](../tools/source_workspace_tool.md). The workflow commits with the [git tool](../tools/git_tool.md).
- Upstream: approved outputs of the [Requirement](requirement_agent.md), [Architecture](architecture_agent.md), [Technology Selection](technology_selection_agent.md) and [Implementation Planning](implementation_planning_agent.md) agents; [Testing Agent](testing_agent.md) analyses in fix mode.
- Downstream: build/test runs, the [Code Review Agent](code_review_agent.md) and [security validation](security_validation_agent.md); a change request goes to the [Impact Analysis Agent](impact_analysis_agent.md).

## Examples

Implementing task `T-1` (task `tests: ["tests/test_calc.py"]`, as in `tests/support/fixtures.py`):

```json
{
  "summary": "add",
  "files": [
    {"path": "calc.py", "action": "create", "content": "def add(a, b):\n    return a + b\n"}
  ],
  "test_code": [
    {"path": "tests/test_calc.py", "action": "create",
     "content": "import unittest\nfrom calc import add\n..."}
  ]
}
```

(The test fixtures put the test file in `files`; that is also accepted, since only
`all_files` and the file's existence matter.)

After `python -m unittest discover -s tests -v` exits 0, the workflow commits
`T-1: Add function`. A fix that passes after code review is committed as
`fix(review): <summary>`.

A change request (from `tests/workflow/test_build_workflow.py`):

```json
{
  "summary": "Plan cannot work",
  "files": [],
  "change_request": {
    "target": "implementation_plan",
    "problem": "unittest discovery needs a tests package",
    "proposed_change": "Add tests/__init__.py to T-1"
  }
}
```

A response that writes `../outside.py` fails validation, and nothing is written.

## Known Limitations

- One structured call per step and no tool-calling loop. The agent sees only the files selected within `limits.max_context_chars`, so large projects may lack context.
- Outside `edits`, every change is a full-file rewrite. Large files cost context and output tokens, and files over 12,000 characters are shown truncated. An edit's `find` must match the file character for character; small models sometimes copy it inexactly.
- **Small local models miss logic bugs.** In the first live full build (todo app, 2026-09-30) `qwen2.5:14b` alone could not fix two logic bugs without a human hint: JSON object keys come back as strings after loading, and ids were reused after a delete (`count + 1`). Both are now in the prompt's pitfalls checklist, the coding profile uses `qwen2.5-coder:14b`, and `factory retry --hint` is the way to unblock the agent when it stalls.
- The prompt rules (follow decisions, write tests, no secrets) are not checked directly. Build/test, code review and security validation catch violations afterwards.
- `security.network_enabled` is not enforced at OS level, so setup commands may download packages.
- `change_request_feedback` is used by the next successful agent call and then dropped; later tasks do not see it.
