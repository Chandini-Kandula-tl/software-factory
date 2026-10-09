# Change-Request Workflow

**Implemented** (workflow `full-lifecycle-1.0`) for the spec §36 trigger: the
Coding Agent discovers during implementation that an approved decision
cannot work. The code is `make_change_request_gate` in
`orchestration/nodes/human_nodes.py` and `_change_request_update` in
`orchestration/nodes/development_nodes.py`. Node/transition reference:
[the execution flow](software_factory_execution_flow.md#13-change-request-flow).

## Flow

```text
implement_task or debug
  Coding Agent returns CodeChanges.change_request instead of (or with) files
  │   target: architecture | technology | implementation_plan
  │   problem, proposed_change, impact
  ▼
Impact Analysis Agent (mode change_request)
  │   inputs: requirements, architecture, implementation plan, the change request,
  │           completed tasks, changed files
  │   success → state.change_request.impact_analysis
  │   failure → impact_analysis_error = "Impact analysis failed; see errors.md",
  │             error added to errors; the request still goes to the human
  ▼
change-request.md / .json written; state.change_request =
  {…request, task_id, origin_status (DEVELOPMENT | BUG_FIXING), raised_at, summary,
   completed_tasks  (task ids already committed),
   changed_files    (git diff --name-only base_commit..HEAD),
   impact_analysis | impact_analysis_error}
  → WAITING_FOR_CHANGE_REQUEST_APPROVAL
  ▼
change_request_approval ⏸  interrupt {kind: approval, stage: change_request,
                                      artifact: change-request.md, target}
  │
  ├─ factory approve <id> [--stage change_request] [--comment "…"]
  │     reopen the target and every later design decision:
  │       architecture        → ARCHITECTURE_ANALYSIS;   drop approvals architecture, technology, implementation_plan
  │       technology          → TECHNOLOGY_SELECTION;    drop approvals technology, implementation_plan
  │       implementation_plan → IMPLEMENTATION_PLANNING; drop approval implementation_plan
  │     revision_feedback[target] = "Approved change request from development.
  │       Problem: … Proposed change: … Reviewer note: <comment>"
  │     dropped stages, plus development, code_review, security_validation and
  │       final_validation, removed from completed_stages;
  │     development.phase = task, development.fix_context = None
  │     ▼
  │   target agent revises (new artifact revision) → its approval gate
  │   → later design stages re-run and are approved again
  │   → plan approved → READY_FOR_DEVELOPMENT → prepare_development
  │       same repository and base commit; tasks already in completed_tasks
  │       are skipped; new/changed task ids are queued
  │
  └─ factory reject <id> --reason "…" [--stage change_request]
        development.change_request_feedback = "Your change request was rejected;
          keep the approved decisions and complete the work within them.
          Reviewer note: <reason>"
        → back to origin_status (DEVELOPMENT → implement_task, BUG_FIXING → debug)
        the Coding Agent gets the note on its next call (then it is cleared)
```

In both cases `state.change_request` is cleared and the decision is written
to `decision_log`, `decisions.md` and execution history
(`human_decision` / `change_request`).

## What the human sees (`change-request.md`)

Rendered by `render_change_request` (`persistence/markdown.py`):

- **Problem** and **Proposed change** from the agent.
- **Impact (Coding Agent's assessment)**: the Coding Agent's `impact` text
  ("_Not stated._" if empty).
- **Impact analysis**: the [Impact Analysis Agent](../agents/impact_analysis_agent.md)'s
  summary, recommendation, affected requirements / components / files / tasks,
  what must be regression-tested and the risks — or "Impact analysis failed;
  see errors.md" if the agent failed.
- **What is affected so far**: deterministic facts from the repository, not
  from the agent: the status it was raised in and the task, the tasks already
  completed and committed, and the files changed since development started
  (up to 40, then "…"). It also states that completed tasks stay committed
  and that development continues with the tasks not completed yet after the
  redesign and a new plan approval.

## Rules

- The LLM only *proposes*. The gate code decides the transition, validated
  by `ALLOWED_TRANSITIONS` (`WAITING_FOR_CHANGE_REQUEST_APPROVAL` →
  the three design stages, `DEVELOPMENT` or `BUG_FIXING`).
- When a response carries a change request, its file changes are **not**
  applied (the agent's output validation warns about this).
- A rejection needs a reason (`ApprovalService`).
- `--stage change_request` guards against approving the wrong gate.
- Approving never discards code: committed tasks stay committed; the plan's
  task ids decide what is re-done. Because the build-phase stages are removed
  from `completed_stages`, `progress.md` shows development, code review,
  security validation and final validation as not done until they pass
  again.
- The old design artifacts stay in `artifacts/<stem>.rev<N>.json`; the
  revision produced for the change is N+1.
- `change-request.md/.json` keeps only the latest request.

- A failed impact analysis never blocks the change request: the human
  decides without it, and the failure is in `errors.md` and execution history
  (stage `change_request`).

Covered by `test_change_request_approved_reopens_planning`,
`test_change_request_rejected_continues_within_decisions` and
`test_change_request_still_reaches_the_human_if_impact_analysis_fails` in
`tests/workflow/test_build_workflow.py`.

## Before development: changing a design stage

- **Before a stage is approved:** `factory reject <id> --reason "…"` revises
  that stage only and returns to the same gate.
- **After a stage is approved and before development:** there is no command
  to reopen it; `factory requirements` is refused once the workflow has
  started. Create a new project (for an existing codebase, with
  `--repository`) whose requirements describe the change.
- **After release:** the project is `COMPLETED`. Describe the next change
  with `factory update <id> --text "…"` (or **Update this app** in the web
  UI) — see [updating a released app](software_factory_execution_flow.md#updating-a-released-app).

## Planned — not implemented yet

- **Human-initiated change requests** and the spec §68.8 flow
  "new requirement on existing software → impact analysis → approval →
  coding → regression testing → review" as its own reusable workflow
  definition. Today there is one fixed graph; the closest equivalent is a new
  existing-mode project (which does run impact analysis, see the
  [existing-project workflow](existing_project_workflow.md)).
- Change requests against the **requirements** (`target` is limited to
  architecture, technology and implementation plan).
