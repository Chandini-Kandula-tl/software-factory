"""Build-phase schemas: code changes, change requests, test analysis, review,
security, verification and validation results.

Agent outputs (``CodeChanges``, ``TestFailureAnalysis``, ``ReviewReport``) are
proposals. Tool results (``CommandRun``, ``VerificationResult``) and the
deterministic reports (``SecurityReport``, ``ValidationReport``) are ground
truth.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

# -- Coding Agent -----------------------------------------------------------------


class FileAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class FileChange(BaseModel):
    path: str = Field(description="Path relative to the repository root, using '/'")
    action: FileAction = FileAction.CREATE
    content: str = Field(default="", description="Full new file content (empty for delete)")


class FileEdit(BaseModel):
    """A targeted change: replace one exact, unique piece of an existing file."""

    path: str = Field(description="Existing file, relative to the repository root")
    find: str = Field(description="Exact text currently in the file; must occur exactly once")
    replace: str = Field(description="Text that replaces it")


class ChangeTarget(StrEnum):
    ARCHITECTURE = "architecture"
    TECHNOLOGY = "technology"
    IMPLEMENTATION_PLAN = "implementation_plan"


class ChangeRequest(BaseModel):
    """Raised by the Coding Agent when an approved decision is not viable."""

    target: ChangeTarget
    problem: str = Field(description="Why the approved decision cannot work")
    proposed_change: str
    impact: str = ""


class CodeChanges(BaseModel):
    summary: str
    files: list[FileChange] = Field(
        default_factory=list, description="Source, configuration and manifest files"
    )
    test_code: list[FileChange] = Field(
        default_factory=list,
        description="Automated test files with real assertions for the behaviour implemented. "
        "Use exactly the file paths listed in the task's `tests` (repository-relative). "
        "Required whenever the task lists tests.",
    )
    edits: list[FileEdit] = Field(
        default_factory=list,
        description="Small targeted changes to existing files (preferred for small fixes)",
    )
    notes: list[str] = Field(default_factory=list)

    @property
    def all_files(self) -> list[FileChange]:
        return [*self.files, *self.test_code]

    @property
    def touched_paths(self) -> list[str]:
        return list(
            dict.fromkeys([*(c.path for c in self.all_files), *(e.path for e in self.edits)])
        )

    change_request: ChangeRequest | None = Field(
        default=None,
        description="Only when an approved architecture/technology/plan decision cannot work",
    )


# -- Testing Agent ----------------------------------------------------------------


class FailureCategory(StrEnum):
    CODE_BUG = "code_bug"
    TEST_BUG = "test_bug"
    BUILD_CONFIGURATION = "build_configuration"
    MISSING_DEPENDENCY = "missing_dependency"
    ENVIRONMENT = "environment"  # needs a human: tool not installed, service down, ...
    UNKNOWN = "unknown"


class TestFailureAnalysis(BaseModel):
    __test__ = False  # not a pytest test class

    summary: str
    failure_category: FailureCategory = FailureCategory.UNKNOWN
    root_cause: str
    suggested_fix: str
    files_to_change: list[str] = Field(default_factory=list)
    failing_tests: list[str] = Field(default_factory=list)


# -- Code Review Agent ------------------------------------------------------------


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


BLOCKING_SEVERITIES = frozenset({Severity.CRITICAL, Severity.HIGH})


class ReviewCategory(StrEnum):
    CORRECTNESS = "correctness"
    REQUIREMENTS = "requirements"
    ARCHITECTURE = "architecture"
    SECURITY = "security"
    TESTS = "tests"
    MAINTAINABILITY = "maintainability"


class ReviewFinding(BaseModel):
    id: str = Field(description="Stable id such as R-1")
    severity: Severity
    category: ReviewCategory
    file: str = ""
    description: str
    suggestion: str = ""


class RequirementCoverage(BaseModel):
    requirement_id: str
    status: str = Field(description="implemented | partial | missing")
    evidence: str = ""


class ReviewReport(BaseModel):
    summary: str
    findings: list[ReviewFinding] = Field(default_factory=list)
    requirement_coverage: list[RequirementCoverage] = Field(default_factory=list)


# -- Tool results (ground truth) ------------------------------------------------


class ProjectCommand(BaseModel):
    argv: list[str] = Field(min_length=1, description="Program and arguments, no shell syntax")
    cwd: str = Field(default=".", description="Directory relative to the repository root")


class CommandRun(BaseModel):
    step: str  # setup | format | lint | build | test | package | audit
    argv: list[str]
    cwd: str
    exit_code: int | None
    timed_out: bool = False
    duration_ms: int = 0
    output_tail: str = ""
    log_file: str | None = None

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class VerificationResult(BaseModel):
    phase: str  # build | test | final
    passed: bool
    runs: list[CommandRun] = Field(default_factory=list)
    failed_step: str | None = None
    timestamp: str


# -- Deterministic reports --------------------------------------------------------


class SecurityFinding(BaseModel):
    severity: Severity
    kind: str  # secret | sensitive_file | dependency_audit | review
    file: str = ""
    description: str


class SecurityReport(BaseModel):
    passed: bool
    findings: list[SecurityFinding] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    timestamp: str


class TraceRow(BaseModel):
    requirement_id: str
    acceptance_criteria: list[str] = Field(default_factory=list)
    tasks: list[str] = Field(default_factory=list)
    commits: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)
    status: str  # covered | not_planned | not_implemented


class ValidationReport(BaseModel):
    passed: bool
    failures: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    verification: VerificationResult | None = None
    traceability: list[TraceRow] = Field(default_factory=list)
    release_artifact: str = ""
    head_commit: str | None = None
    # Files the final test run itself created or changed (not committed code).
    test_side_effects: list[str] = Field(default_factory=list)
    timestamp: str


class ReleaseRecord(BaseModel):
    tag: str
    commit: str | None
    release_artifact: str
    repository: str
    released_at: str


# -- Impact Analysis Agent ---------------------------------------------------------


class ImpactAnalysis(BaseModel):
    """What a change touches, and what must be re-checked (spec §36, §68.7)."""

    summary: str
    affected_requirements: list[str] = Field(default_factory=list)
    affected_components: list[str] = Field(default_factory=list)
    affected_files: list[str] = Field(default_factory=list)
    affected_tasks: list[str] = Field(default_factory=list)
    regression_areas: list[str] = Field(
        default_factory=list,
        description="Existing behaviour that must be re-tested because of this change",
    )
    risks: list[str] = Field(default_factory=list)
    recommendation: str = Field(description="What the human should decide and why")
