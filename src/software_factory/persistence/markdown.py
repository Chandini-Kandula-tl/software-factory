"""Render domain objects into the human-readable project Markdown artifacts."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from software_factory.domain.architecture import ArchitectureProposal
from software_factory.domain.development import (
    ChangeRequest,
    CommandRun,
    ImpactAnalysis,
    ReleaseRecord,
    ReviewReport,
    SecurityReport,
    TestFailureAnalysis,
    ValidationReport,
    VerificationResult,
)
from software_factory.domain.planning import ImplementationPlan
from software_factory.domain.project import Project
from software_factory.domain.repository import RepositoryAnalysis
from software_factory.domain.requirements import ClarificationAnswer, RequirementAnalysis
from software_factory.domain.technology import TechnologySelection

# Stage order used for progress reporting.
PROGRESS_STAGES: list[tuple[str, str]] = [
    ("requirements", "Requirements analysis & approval"),
    ("repository_analysis", "Repository analysis"),
    ("architecture", "Architecture design & approval"),
    ("technology", "Technology selection & approval"),
    ("implementation_plan", "Implementation planning & approval"),
    ("development", "Development: every planned task coded, built and tested"),
    ("code_review", "Code review"),
    ("security_validation", "Security validation"),
    ("final_validation", "Final validation"),
    ("release", "Release approval & release"),
]


def _bullets(items: Iterable[str], empty: str = "_None._") -> str:
    lines = [f"- {item}" for item in items if item]
    return "\n".join(lines) if lines else empty


def render_project(project: Project, lifecycle_status: str) -> str:
    return f"""# {project.name}

| Field | Value |
|---|---|
| Project id | `{project.project_id}` |
| Mode | {project.mode.value} |
| Created | {project.created_at} |
| Repository | {project.repository_path or "_none (greenfield)_"} |
| Factory version | {project.factory_version} |
| Current status | **{lifecycle_status}**{" (paused)" if project.paused else ""} |

## Description

{project.description or "_No description provided._"}
"""


def render_requirements(
    analysis: RequirementAnalysis,
    clarifications: list[ClarificationAnswer],
    revision: int,
) -> str:
    def items(reqs: list[Any]) -> str:
        if not reqs:
            return "_None identified._"
        return "\n".join(
            f"- **{r.id}** ({r.priority}, source: {r.source}): {r.description}" for r in reqs
        )

    criteria = (
        "\n".join(
            f"- **{c.id}** ({', '.join(c.requirement_ids) or 'general'}): {c.description}"
            for c in analysis.acceptance_criteria
        )
        or "_None defined._"
    )
    answered = (
        "\n".join(
            f"- **{a.question_id}** (round {a.round}): {a.question}\n  - Answer: {a.answer}"
            for a in clarifications
        )
        or "_No clarifications were needed._"
    )
    return f"""# Requirements

_Revision {revision}_

## Summary

{analysis.summary}

**Application type:** {analysis.application_type or "_not stated_"}

## Functional requirements

{items(analysis.functional_requirements)}

## Non-functional requirements

{items(analysis.non_functional_requirements)}

## Constraints

{_bullets(analysis.constraints)}

## Assumptions

{_bullets(analysis.assumptions)}

## Acceptance criteria

{criteria}

## Open questions

{_bullets(analysis.open_questions)}

## Clarifications

{answered}
"""


def render_architecture(proposal: ArchitectureProposal, revision: int) -> str:
    components = "\n".join(
        f"### {c.name}\n\n{c.responsibility}\n\n"
        f"- Interfaces: {', '.join(c.interfaces) or 'n/a'}\n"
        f"- Satisfies: {', '.join(c.requirement_ids) or 'n/a'}\n"
        for c in proposal.components
    )
    alternatives = (
        "\n".join(
            f"- **{a.name}** — {a.summary}"
            + (f" _Not chosen: {a.reason_not_chosen}_" if a.reason_not_chosen else "")
            for a in proposal.alternatives
        )
        or "_None considered._"
    )
    return f"""# Architecture

_Revision {revision}_

**Style:** {proposal.style}

## Overview

{proposal.overview}

## Components

{components}
## Boundaries

{_bullets(proposal.boundaries)}

## Data flow

{_bullets(proposal.data_flow)}

## External integrations

{_bullets(proposal.external_integrations)}

## Security considerations

{_bullets(proposal.security_considerations)}

## Scalability considerations

{_bullets(proposal.scalability_considerations)}

## Advantages

{_bullets(proposal.advantages)}

## Risks

{_bullets(proposal.risks)}

## Alternatives

{alternatives}

## Trade-offs

{_bullets(proposal.tradeoffs)}
"""


def render_technology(selection: TechnologySelection, revision: int) -> str:
    rows = "\n".join(
        f"| {c.category} | {c.choice} | {c.rationale} | "
        f"{', '.join(c.alternatives_considered) or '—'} |"
        for c in selection.choices
    )
    return f"""# Technology stack

_Revision {revision}_

## Summary

{selection.summary}

## Choices

| Category | Choice | Rationale | Alternatives considered |
|---|---|---|---|
{rows}

## Not applicable

{_bullets(selection.not_applicable)}

## Trade-offs

{_bullets(selection.tradeoffs)}

## Risks

{_bullets(selection.risks)}

## Existing-project compatibility

{selection.existing_project_compatibility or "_Greenfield project._"}
"""


def render_plan(
    plan: ImplementationPlan, revision: int, environment_checks: list[str] | None = None
) -> str:
    milestones = (
        "\n".join(f"- **{m.name}**: {', '.join(m.task_ids)}" for m in plan.milestones)
        or "_None defined._"
    )
    tasks = "\n".join(
        f"### {t.id} — {t.title}\n\n{t.description}\n\n"
        f"- Depends on: {', '.join(t.depends_on) or 'none'}\n"
        f"- Requirements: {', '.join(t.requirement_ids) or 'n/a'}\n"
        f"- Acceptance criteria: {', '.join(t.acceptance_criteria_ids) or 'n/a'}\n"
        f"- Likely files: {', '.join(t.likely_files) or 'n/a'}\n"
        f"- Tests: {', '.join(t.tests) or 'n/a'}\n"
        for t in plan.tasks
    )
    command_rows = "\n".join(
        f"| {step} | `{' '.join(c['argv'])}` | {c['cwd']} |"
        for step, commands in plan.commands.model_dump().items()
        for c in commands
    )
    checks = (
        "\n## ⚠️ Environment check\n\n"
        "Approving this plan with these problems means development will stop at once:\n\n"
        + _bullets(environment_checks)
        + "\n"
        if environment_checks
        else "\n## Environment check\n\nEvery command's program is allowed and installed.\n"
    )
    return f"""# Implementation plan

_Revision {revision}_

## Summary

{plan.summary}

**Release artifact:** {plan.release_artifact}

## Commands the factory will run

Approving the plan approves exactly these commands (run from the repository root
unless a directory is given). Tests pass only when `test` exits with code 0.

| Step | Command | Directory |
|---|---|---|
{command_rows}
{checks}
## Milestones

{milestones}

## Tasks

{tasks}
## Risks

{_bullets(plan.risks)}
"""


def render_repository_analysis(analysis: RepositoryAnalysis) -> str:
    languages = (
        "\n".join(f"- {lang}: {count} file(s)" for lang, count in analysis.languages.items())
        or "_None detected._"
    )
    git = analysis.git
    return f"""# Repository analysis

Deterministic facts gathered by the repository-analysis tool.

**Path:** `{analysis.path}`
**Files scanned:** {analysis.total_files_scanned}

## Languages

{languages}

## Frameworks

{_bullets(analysis.frameworks)}

## Package managers

{_bullets(analysis.package_managers)}

## Build systems

{_bullets(analysis.build_systems)}

## Dependency files

{_bullets(analysis.dependency_files)}

## Tests

{_bullets(analysis.test_locations)}

## Configuration

{_bullets(analysis.configuration_files)}

## Likely entry points

{_bullets(analysis.likely_entry_points)}

## Top-level structure

{_bullets(analysis.top_level_structure)}

## Git

- Repository: {git.is_repository}
- Branch: {git.branch or "n/a"}
- Commit: {git.commit or "n/a"}
- Clean working tree: {git.clean if git.clean is not None else "n/a"}
- Changed files: {git.changed_files}
"""


def render_progress(state: dict[str, Any], paused: bool, pending_action: str) -> str:
    completed = set(state.get("completed_stages", []))
    lines = []
    next_stage = None
    for key, label in PROGRESS_STAGES:
        done = key in completed
        if not done and next_stage is None:
            next_stage = label
        lines.append(f"- [{'x' if done else ' '}] {label}")
    development = state.get("development") or {}
    if development:
        finished = len(development.get("completed_tasks", []))
        total = finished + len(development.get("task_queue", []))
        current = (development.get("task_queue") or ["—"])[0]
        lines.append(
            f"\n**Tasks:** {finished}/{total} done · current: {current} · "
            f"fix attempts: {development.get('debug_iterations', 0)} · "
            f"code in `{development.get('source_path', '—')}`"
        )
    failure = state.get("failure")
    open_issues = [f"{failure['stage']}: {failure['message']}"] if failure else []
    open_issues += [
        f"Open question: {q}" for q in (state.get("requirements") or {}).get("open_questions", [])
    ]
    return f"""# Progress

| Field | Value |
|---|---|
| Current stage | **{state.get("lifecycle_status", "CREATED")}**{" (paused)" if paused else ""} |
| Pending action | {pending_action} |
| Next stage | {next_stage or "—"} |
| Last execution | {state.get("last_execution_id") or "—"} |
| Last checkpoint | see `checkpoints/` (LangGraph thread `{state.get("project_id", "")}`) |

## Stages

{chr(10).join(lines)}

## Open issues

{_bullets(open_issues)}
"""


def render_decisions(decision_log: list[dict[str, Any]]) -> str:
    if not decision_log:
        return "# Decisions\n\n_No decisions recorded yet._\n"
    entries = "\n".join(
        f"- {d['timestamp']} — **{d['stage']}** {d['status']} by {d.get('approved_by', 'human')}"
        f" (revision {d.get('artifact_revision', 1)})"
        + (f": {d['reason']}" if d.get("reason") else "")
        for d in decision_log
    )
    return f"# Decisions\n\nHuman approval decisions, oldest first.\n\n{entries}\n"


def render_errors(errors: list[dict[str, Any]]) -> str:
    if not errors:
        return "# Errors\n\n_No errors recorded._\n"
    entries = "\n".join(
        f"- {e['timestamp']} — **{e['stage']}** [{e.get('error_kind', 'error')}] {e['message']}"
        + (f"\n  - Hint: {e['hint']}" if e.get("hint") else "")
        for e in errors
    )
    return f"# Errors\n\n{entries}\n"


# -- build phase ------------------------------------------------------------------


def _run_line(run: CommandRun) -> str:
    result = "✅ pass" if run.success else ("⏱ timed out" if run.timed_out else "❌ fail")
    log = f" — log: `{run.log_file}`" if run.log_file else ""
    return (
        f"| {run.step} | `{' '.join(run.argv)}` | {run.cwd} | {run.exit_code} | "
        f"{run.duration_ms / 1000:.1f}s | {result}{log} |"
    )


def render_verification(verification: VerificationResult, title: str = "Test results") -> str:
    rows = "\n".join(_run_line(r) for r in verification.runs) or "| — | — | — | — | — | — |"
    verdict = "PASSED" if verification.passed else f"FAILED at step `{verification.failed_step}`"
    tail = ""
    if verification.runs and not verification.passed:
        tail = (
            "\n## Output of the failing command (tail)\n\n```text\n"
            + verification.runs[-1].output_tail[-3000:]
            + "\n```\n"
        )
    return f"""# {title}

Real command results (exit codes from the tools, not from an agent).

**Phase:** {verification.phase} · **Result:** {verdict} · **When:** {verification.timestamp}

| Step | Command | Directory | Exit code | Duration | Result |
|---|---|---|---|---|---|
{rows}
{tail}"""


def render_test_analysis(analysis: TestFailureAnalysis) -> str:
    return f"""# Test failure analysis

_Produced by the Testing Agent from the real command output._

**Category:** {analysis.failure_category.value}

## Summary

{analysis.summary}

## Root cause

{analysis.root_cause}

## Suggested fix

{analysis.suggested_fix}

**Files to change:** {", ".join(analysis.files_to_change) or "—"}

**Failing tests:** {", ".join(analysis.failing_tests) or "—"}
"""


def render_review(report: ReviewReport, revision: int) -> str:
    findings = (
        "\n".join(
            f"| {f.id} | {f.severity.value} | {f.category.value} | {f.file or '—'} | "
            f"{f.description} | {f.suggestion or '—'} |"
            for f in report.findings
        )
        or "| — | — | — | — | No findings | — |"
    )
    coverage = (
        "\n".join(
            f"| {c.requirement_id} | {c.status} | {c.evidence or '—'} |"
            for c in report.requirement_coverage
        )
        or "| — | — | — |"
    )
    return f"""# Code review

_Revision {revision}. Critical and high findings block the release and are sent back for fixing._

## Summary

{report.summary}

## Findings

| Id | Severity | Category | File | Finding | Suggestion |
|---|---|---|---|---|---|
{findings}

## Requirement coverage

| Requirement | Status | Evidence |
|---|---|---|
{coverage}
"""


def render_security(report: SecurityReport) -> str:
    findings = (
        "\n".join(
            f"| {f.severity.value} | {f.kind} | {f.file or '—'} | {f.description} |"
            for f in report.findings
        )
        or "| — | — | — | No findings |"
    )
    return f"""# Security validation

**Result:** {"PASSED" if report.passed else "FAILED"} · **When:** {report.timestamp}

Deterministic checks decide the result; code-review security findings are listed
for information.

## Checks run

{_bullets(report.checks)}

## Findings

| Severity | Kind | Location | Description |
|---|---|---|---|
{findings}

## Notes

{_bullets(report.notes)}
"""


def render_validation(report: ValidationReport) -> str:
    trace = (
        "\n".join(
            f"| {r.requirement_id} | {', '.join(r.acceptance_criteria) or '—'} | "
            f"{', '.join(r.tasks) or '—'} | {', '.join(r.commits) or '—'} | "
            f"{', '.join(r.tests) or '—'} | {r.status} |"
            for r in report.traceability
        )
        or "| — | — | — | — | — | — |"
    )
    verification = (
        render_verification(report.verification, "Final build and test run").replace(
            "# Final", "## Final", 1
        )
        if report.verification
        else "_No final run._"
    )
    return f"""# Final validation

**Result:** {"PASSED — ready for release approval" if report.passed else "FAILED"}
· **Commit:** `{report.head_commit or "—"}` · **When:** {report.timestamp}

**Release artifact (from the plan):** {report.release_artifact}

## Failures

{_bullets(report.failures)}

## Warnings

{_bullets(report.warnings)}

{verification}

## Traceability

Requirement → acceptance criteria → tasks → commits → tests.

| Requirement | Acceptance criteria | Tasks | Commits | Tests | Status |
|---|---|---|---|---|---|
{trace}
"""


def render_impact_analysis(analysis: ImpactAnalysis, title: str = "# Impact analysis") -> str:
    return f"""{title}

_Produced by the Impact Analysis Agent from deterministic facts._

{analysis.summary}

**Recommendation:** {analysis.recommendation}

- Affected requirements: {", ".join(analysis.affected_requirements) or "—"}
- Affected components: {", ".join(analysis.affected_components) or "—"}
- Affected files: {", ".join(analysis.affected_files) or "—"}
- Affected tasks: {", ".join(analysis.affected_tasks) or "—"}

**Must be regression-tested:**

{_bullets(analysis.regression_areas)}

**Risks:**

{_bullets(analysis.risks)}
"""


def render_change_request(request: ChangeRequest, context: dict[str, Any] | None = None) -> str:
    context = context or {}
    if context.get("impact_analysis"):
        impact = render_impact_analysis(
            ImpactAnalysis.model_validate(context["impact_analysis"]), "## Impact analysis"
        )
    else:
        impact = (
            "## Impact analysis\n\n"
            + context.get("impact_analysis_error", "_Not available._")
            + "\n"
        )
    done = context.get("completed_tasks") or []
    changed = context.get("changed_files") or []
    files = ", ".join(changed[:40]) or "none"
    if len(changed) > 40:
        files += " …"
    affected = f"""## What is affected so far

Deterministic facts from the repository (not from the agent):

- Raised during: {context.get("origin_status", "—")} (task {context.get("task_id") or "—"})
- Tasks already completed and committed: {", ".join(done) or "none"}
- Files changed since development started: {files}

Completed tasks stay committed. After the redesign and a new plan approval,
development continues with the tasks that are not completed yet.
"""
    return f"""# Change request

The Coding Agent found that an approved decision cannot work as planned.
Approve to reopen the **{request.target.value}** stage (it and later stages are
revised and approved again), or reject to make the agent continue within the
approved decisions.

## Problem

{request.problem}

## Proposed change

{request.proposed_change}

## Impact (Coding Agent's assessment)

{request.impact or "_Not stated._"}

{impact}
{affected}"""


def render_release_notes(record: ReleaseRecord, state: dict[str, Any]) -> str:
    completed = (state.get("development") or {}).get("completed_tasks", [])
    tasks = {t["id"]: t["title"] for t in (state.get("implementation_plan") or {}).get("tasks", [])}
    task_lines = [
        f"{t['task_id']} — {tasks.get(t['task_id'], '')} (`{str(t.get('commit'))[:10]}`)"
        for t in completed
    ]
    requirements = state.get("requirements") or {}
    reqs = [
        f"{r['id']}: {r['description']}"
        for r in requirements.get("functional_requirements", [])
        + requirements.get("non_functional_requirements", [])
    ]
    decisions = [
        f"{d['stage']} {d['status']} ({d['timestamp']})" for d in state.get("decision_log", [])
    ]
    return f"""# Release notes — {state.get("project_name", "")}

| Field | Value |
|---|---|
| Tag | `{record.tag}` |
| Commit | `{record.commit or "—"}` |
| Released | {record.released_at} |
| Repository | `{record.repository}` |
| Release artifact | {record.release_artifact} |

## Requirements delivered

{_bullets(reqs)}

## Implemented tasks

{_bullets(task_lines)}

## Human decisions

{_bullets(decisions)}

See `validation.md` (tests and traceability), `review.md` and `security.md`.
"""
