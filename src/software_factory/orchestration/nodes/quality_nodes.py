"""Quality-phase workflow nodes: code review, security validation, final
validation and release.

    code_review ─┬─ blocking findings → debug → build → test → code_review   (≤ max_review_rounds)
                 └─ ok → security_validation
    security_validation ─┬─ blocking findings → debug … → security_validation
                         └─ clean → final_validation
    final_validation ─┬─ fail → debug … → final_validation
                      └─ pass → release_approval (human) → release → COMPLETED

The deterministic parts (secret scan, final re-run, traceability, release
tag) never depend on an agent's opinion. Shared helpers live in
``development_nodes``.
"""

from __future__ import annotations

from typing import Any

from software_factory.domain.development import (
    BLOCKING_SEVERITIES,
    ReleaseRecord,
    ReviewReport,
    SecurityFinding,
    SecurityReport,
    Severity,
    TraceRow,
    ValidationReport,
    VerificationResult,
)
from software_factory.domain.errors import FactoryError, QualityGateError
from software_factory.domain.execution import ExecutionKind, ExecutionStatus
from software_factory.domain.lifecycle import LifecycleStatus as S
from software_factory.domain.planning import ImplementationPlan
from software_factory.domain.quality_gates import require_entry
from software_factory.observability.clock import utc_now
from software_factory.orchestration.nodes.common import (
    Timer,
    execution_record,
    fail_stage,
    invoke_agent,
)
from software_factory.orchestration.nodes.development_nodes import (
    NodeFn,
    ProjectCommand,
    _dev,
    _git,
    _plan,
    _run_commands,
    _source,
    _to,
    _verify,
)
from software_factory.orchestration.ports import WorkflowDependencies
from software_factory.orchestration.state import FactoryState
from software_factory.tools.secret_scan import is_sensitive_file, scan_text
from software_factory.tools.source_workspace import list_source_files, manifest_fingerprint


def make_code_review(deps: WorkflowDependencies) -> NodeFn:
    def code_review(state: FactoryState) -> dict[str, Any]:
        timer = Timer()
        dev = _dev(state)
        try:
            require_entry("code_review", state)
        except QualityGateError as error:
            return fail_stage(deps, state, stage="code_review", error=error, timer=timer)
        source = _source(dev)
        git = _git(deps, source)
        base = dev.get("base_commit")
        diff = git.run(["diff", f"{base}..HEAD"] if base else ["show", "HEAD"]).stdout
        budget = deps.limits.max_context_chars
        if len(diff) > budget:
            diff = diff[:budget] + "\n… [diff truncated]"
        previous = state.get("review")
        result, update = invoke_agent(
            deps,
            state,
            deps.agents.code_review,
            stage="code_review",
            available={
                "requirements": state.get("requirements"),
                "architecture": state.get("architecture"),
                "technology_selection": state.get("technology_selection"),
                "implementation_plan": state.get("implementation_plan"),
                "changes": diff or "No changes.",
                "file_tree": list_source_files(source),
                "test_results": _verification_summary(state.get("last_verification")),
                "previous_review": (previous or {}).get("report"),
            },
        )
        if result is None:
            return update
        report: ReviewReport = result.output
        blocking = [f for f in report.findings if f.severity in BLOCKING_SEVERITIES]
        rounds = dev.get("review_rounds", 0)
        revisions = dict(state.get("artifact_revisions") or {})
        revisions["code_review"] = revisions.get("code_review", 0) + 1
        review_state = {
            "report": report.model_dump(mode="json"),
            "passed": not blocking,
            "blocking_findings": [f.id for f in blocking],
            "round": rounds,
        }
        update["artifact_revisions"] = revisions
        deps.recorder.write_stage_artifact(
            "code_review", report, {**state, "artifact_revisions": revisions}
        )

        if blocking and rounds < deps.limits.max_review_rounds:
            dev.update(
                {
                    "review_rounds": rounds + 1,
                    "phase": "review",
                    "debug_iterations": 0,
                    "fix_context": {
                        "source": "review",
                        "findings": [f.model_dump(mode="json") for f in blocking],
                    },
                }
            )
            return {
                **update,
                "review": review_state,
                "development": dev,
                "lifecycle_status": _to(state, S.BUG_FIXING),
            }
        warnings = list(state.get("warnings", []))
        if blocking:
            warnings.append(
                f"Code review round limit ({deps.limits.max_review_rounds}) reached with "
                f"{len(blocking)} blocking finding(s) open: {', '.join(f.id for f in blocking)}. "
                "They are listed in review.md for the release decision."
            )
        return {
            **update,
            "review": review_state,
            "warnings": warnings,
            "completed_stages": _add_stage(state, "code_review"),
            "lifecycle_status": _to(state, S.SECURITY_VALIDATION),
        }

    return code_review


def _record_step(
    deps: WorkflowDependencies, state: FactoryState, stage: str, passed: bool, summary: str
) -> None:
    deps.recorder.record_execution(
        execution_record(
            deps,
            state,
            kind=ExecutionKind.WORKFLOW,
            stage=stage,
            status=ExecutionStatus.SUCCESS if passed else ExecutionStatus.FAILED,
            summary=summary,
        )
    )


def _add_stage(state: FactoryState, stage: str) -> list[str]:
    stages = list(state.get("completed_stages") or [])
    return stages if stage in stages else [*stages, stage]


def _verification_summary(raw: dict[str, Any] | None) -> str:
    if not raw:
        return "No test run recorded."
    verification = VerificationResult.model_validate(raw)
    lines = [f"{verification.phase}: {'PASSED' if verification.passed else 'FAILED'}"]
    for run in verification.runs:
        lines.append(f"- {run.step}: {' '.join(run.argv)} → exit {run.exit_code}")
    last = verification.runs[-1].output_tail[-1500:] if verification.runs else ""
    return "\n".join(lines) + ("\n\nLast output:\n" + last if last else "")


def make_security_validation(deps: WorkflowDependencies) -> NodeFn:
    def security_validation(state: FactoryState) -> dict[str, Any]:
        dev = _dev(state)
        plan = _plan(state)
        source = _source(dev)
        git = _git(deps, source)
        findings: list[SecurityFinding] = []
        files = git.tracked_files()
        for relative in files:
            if is_sensitive_file(relative):
                findings.append(
                    SecurityFinding(
                        severity=Severity.CRITICAL,
                        kind="sensitive_file",
                        file=relative,
                        description="Sensitive file committed (keys/credentials/.env)",
                    )
                )
                continue
            path = source / relative
            if not path.is_file() or path.stat().st_size > 1_000_000:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for match in scan_text(relative, text):
                severity = (
                    Severity.MEDIUM
                    if match.in_test_code and match.kind == "hard-coded credential"
                    else Severity.HIGH
                    if match.kind == "hard-coded credential"
                    else Severity.CRITICAL
                )
                findings.append(
                    SecurityFinding(
                        severity=severity,
                        kind="secret",
                        file=f"{relative}:{match.line}",
                        description=f"Possible {match.kind}",
                    )
                )
        checks = [f"Secret and sensitive-file scan of {len(files)} tracked file(s)"]
        notes: list[str] = []
        if plan.commands.audit:
            runs = _run_commands(deps, state, source, "audit", plan.commands.audit)
            checks.append("Dependency audit: " + ", ".join(" ".join(r.argv) for r in runs))
            failed = [r for r in runs if not r.success]
            if failed:
                findings.append(
                    SecurityFinding(
                        severity=Severity.MEDIUM,
                        kind="dependency_audit",
                        description=f"Dependency audit reported problems (exit "
                        f"{failed[0].exit_code}); see {failed[0].log_file}",
                    )
                )
        else:
            notes.append("No dependency audit command in the plan; audit not run.")
        review = (state.get("review") or {}).get("report") or {}
        for finding in review.get("findings", []):
            if finding.get("category") == "security":
                findings.append(
                    SecurityFinding(
                        severity=Severity(finding["severity"]),
                        kind="review",
                        file=finding.get("file", ""),
                        description=f"{finding['id']}: {finding['description']}",
                    )
                )
        # Only deterministic findings block; review findings are handled by the
        # review loop and shown here for the release decision.
        blocking = [f for f in findings if f.severity in BLOCKING_SEVERITIES and f.kind != "review"]
        report = SecurityReport(
            passed=not blocking, findings=findings, checks=checks, notes=notes, timestamp=utc_now()
        )
        deps.recorder.write_stage_artifact("security", report, state)
        _record_step(
            deps,
            state,
            "security_validation",
            report.passed,
            f"Security validation {'passed' if report.passed else 'failed'}: "
            f"{len(report.findings)} finding(s)",
        )
        security_state = report.model_dump(mode="json")
        rounds = dev.get("security_rounds", 0)
        if blocking and rounds < deps.limits.max_review_rounds:
            dev.update(
                {
                    "security_rounds": rounds + 1,
                    "phase": "security",
                    "debug_iterations": 0,
                    "fix_context": {
                        "source": "security",
                        "findings": [f.model_dump(mode="json") for f in blocking],
                    },
                }
            )
            return {
                "security": security_state,
                "development": dev,
                "lifecycle_status": _to(state, S.BUG_FIXING),
            }
        if blocking:
            return {
                "security": security_state,
                **fail_stage(
                    deps,
                    state,
                    stage="security_validation",
                    error=FactoryError(
                        f"{len(blocking)} blocking security finding(s) remain after "
                        f"{rounds} fix round(s)",
                        hint="See security.md, remove the secrets by hand, then `factory retry`.",
                    ),
                ),
            }
        return {
            "security": security_state,
            "completed_stages": _add_stage(state, "security_validation"),
            "lifecycle_status": _to(state, S.FINAL_VALIDATION),
        }

    return security_validation


def make_final_validation(deps: WorkflowDependencies) -> NodeFn:
    def final_validation(state: FactoryState) -> dict[str, Any]:
        dev = _dev(state)
        plan = _plan(state)
        source = _source(dev)
        git = _git(deps, source)
        steps: list[tuple[str, list[ProjectCommand]]] = []
        fingerprint = manifest_fingerprint(source)
        if plan.commands.setup and fingerprint != dev.get("setup_fingerprint"):
            steps.append(("setup", plan.commands.setup))
        steps += [
            ("lint", plan.commands.lint),
            ("build", plan.commands.build),
            ("test", plan.commands.test),
            ("package", plan.commands.package),
        ]
        uncommitted = git.changed_paths()
        verification = _verify(deps, state, source, "final", steps)
        side_effects = sorted(set(git.changed_paths()) - set(uncommitted))
        failures: list[str] = []
        warnings: list[str] = []
        if not verification.passed:
            failures.append(f"Final {verification.failed_step} run failed")
        if dev.get("task_queue"):
            failures.append(f"Tasks not implemented: {', '.join(dev['task_queue'])}")
        git_state = git.state()
        if uncommitted:
            failures.append(
                f"{len(uncommitted)} uncommitted change(s) in the repository: "
                + ", ".join(uncommitted[:10])
            )
        if side_effects:
            warnings.append(
                "The test run itself created or changed files (tests should use temporary "
                "folders): " + ", ".join(side_effects[:10])
            )
        trace = _traceability(state, plan, dev)
        for row in trace:
            if row.status == "not_planned":
                warnings.append(f"{row.requirement_id} has no planned task")
            elif row.status == "not_implemented":
                failures.append(f"{row.requirement_id} has unfinished tasks")
            elif not row.tests:
                warnings.append(f"{row.requirement_id} has no named test in the plan")
        review = state.get("review") or {}
        if review and not review.get("passed"):
            warnings.append(
                f"Code review left blocking findings open: {review.get('blocking_findings')}"
            )
        report = ValidationReport(
            passed=not failures,
            failures=failures,
            warnings=warnings,
            verification=verification,
            traceability=trace,
            release_artifact=plan.release_artifact,
            head_commit=git_state.commit,
            test_side_effects=side_effects,
            timestamp=utc_now(),
        )
        deps.recorder.write_stage_artifact("validation", report, state)
        _record_step(
            deps,
            state,
            "final_validation",
            report.passed,
            "Final validation passed"
            if report.passed
            else "Final validation failed: " + "; ".join(failures),
        )
        update: dict[str, Any] = {
            "validation": report.model_dump(mode="json"),
            "last_verification": verification.model_dump(mode="json"),
        }
        if report.passed:
            return {
                **update,
                "completed_stages": _add_stage(state, "final_validation"),
                "lifecycle_status": _to(state, S.WAITING_FOR_RELEASE_APPROVAL),
            }
        if dev.get("debug_iterations", 0) < deps.limits.max_debug_iterations:
            dev["phase"] = "validation"
            dev["fix_context"] = (
                {"source": "verification", "verification": verification.model_dump(mode="json")}
                if not verification.passed
                else {"source": "validation", "findings": failures}
            )
            return {**update, "development": dev, "lifecycle_status": _to(state, S.BUG_FIXING)}
        return {
            **update,
            **fail_stage(
                deps,
                state,
                stage="final_validation",
                error=FactoryError(
                    "Final validation failed: " + "; ".join(failures),
                    hint="See validation.md, fix by hand, then `factory retry`.",
                ),
            ),
        }

    return final_validation


def _traceability(
    state: FactoryState, plan: ImplementationPlan, dev: dict[str, Any]
) -> list[TraceRow]:
    requirements = state.get("requirements") or {}
    criteria = requirements.get("acceptance_criteria", [])
    commits = {t["task_id"]: t["commit"] for t in dev.get("completed_tasks", [])}
    rows = []
    for requirement in requirements.get("functional_requirements", []) + requirements.get(
        "non_functional_requirements", []
    ):
        rid = requirement["id"]
        ac_ids = [c["id"] for c in criteria if rid in c.get("requirement_ids", [])]
        tasks = [
            t
            for t in plan.tasks
            if rid in t.requirement_ids or set(ac_ids) & set(t.acceptance_criteria_ids)
        ]
        if not tasks:
            status = "not_planned"
        elif all(t.id in commits for t in tasks):
            status = "covered"
        else:
            status = "not_implemented"
        rows.append(
            TraceRow(
                requirement_id=rid,
                acceptance_criteria=ac_ids,
                tasks=[t.id for t in tasks],
                commits=[commits[t.id][:10] for t in tasks if commits.get(t.id)],
                tests=[test for t in tasks for test in t.tests],
                status=status,
            )
        )
    return rows


def make_release(deps: WorkflowDependencies) -> NodeFn:
    def release(state: FactoryState) -> dict[str, Any]:
        timer = Timer()
        try:
            require_entry("release", state)
        except QualityGateError as error:
            return fail_stage(deps, state, stage="release", error=error, timer=timer)
        dev = _dev(state)
        plan = _plan(state)
        source = _source(dev)
        git = _git(deps, source)
        existing = set(git.run(["tag", "--list", "release-*"]).stdout.split())
        number = 1
        while f"release-{number}" in existing:
            number += 1
        tag = f"release-{number}"
        tagged = git.tag(tag, f"{state['project_name']} — released by Software Factory")
        if not tagged.success:
            return fail_stage(
                deps,
                state,
                stage="release",
                timer=timer,
                error=FactoryError(f"git tag failed: {tagged.stderr.strip()}"),
            )
        record = ReleaseRecord(
            tag=tag,
            commit=git.head(),
            release_artifact=plan.release_artifact,
            repository=str(source),
            released_at=utc_now(),
        )
        deps.recorder.write_stage_artifact("release", record, state)
        deps.recorder.record_execution(
            execution_record(
                deps,
                state,
                kind=ExecutionKind.WORKFLOW,
                stage="release",
                status=ExecutionStatus.SUCCESS,
                timer=timer,
                summary=f"Released {tag} at {record.commit}",
            )
        )
        return {
            "release": record.model_dump(mode="json"),
            "completed_stages": _add_stage(state, "release"),
            "lifecycle_status": _to(state, S.COMPLETED),
        }

    return release
