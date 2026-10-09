# Security Validation (deterministic step)

**Status:** Implemented (build phase) as a **deterministic workflow step — there is no LLM agent** · **Code:** `security_validation` in [`orchestration/nodes/quality_nodes.py`](../../src/software_factory/orchestration/nodes/quality_nodes.py), [`tools/secret_scan.py`](../../src/software_factory/tools/secret_scan.py) · **Spec:** §17, §68.9, §68.12, §73.2

## Agent Name

None. The workflow node `security_validation` does this job with deterministic code
(spec §68.9 splits responsibilities along the AI-vs-deterministic boundary). The file
keeps its name so the agent catalog stays complete.

## Purpose

Check the generated code for security problems that tools can prove (committed secrets,
sensitive files and, optionally, vulnerable dependencies) and block the release until
they are fixed. AI review never replaces these checks (spec §17): the Code Review
Agent's security findings are listed for information only.

## Responsibilities

1. **Sensitive-file check** of every tracked file (`git ls-files`). The names `.env`, `id_rsa`, `id_ed25519` and `credentials.json`, and the suffixes `.pem`, `.key`, `.p12` and `.pfx`, are `critical`.
2. **Secret scan** of every other tracked file that is UTF-8 text and at most 1 MB, using `scan_text` (see the [secret scan tool](../tools/secret_scan_tool.md)).
3. **Dependency audit:** runs the plan's `commands.audit` (if any) with `DependencyAuditTool` (`tool_for_step("audit", …)`, on top of the shell tool; history records carry `details.tool = "dependency_audit"`). A non-zero exit or a timeout adds one `medium` `dependency_audit` finding that points at the log. With no audit command, a note says "audit not run".
4. **Review findings:** every Code Review finding with `category == "security"` is copied as kind `review` with its own severity. These are **informational and never block**.

## Inputs

This step takes no `AgentContext`. The node reads:

- `development.source_path`;
- the tracked files in the source repository;
- `implementation_plan.commands.audit`;
- `review.report`.

## Input Schema

`ImplementationPlan.commands.audit: list[ProjectCommand]` (`argv`, `cwd`) and
`ReviewReport` ([`domain/development.py`](../../src/software_factory/domain/development.py)).

## Output

A `SecurityReport`, written as `security.json` / `security.md` (latest version only) and
stored in state `security`. Audit logs go to `artifacts/tool-runs/NNN-audit.log`.

## Output Schema

`SecurityReport` has these fields:

- `passed`;
- `findings: list[SecurityFinding]`;
- `checks: list[str]`;
- `notes: list[str]`;
- `timestamp`.

`SecurityFinding` has these fields:

- `severity` (`critical` / `high` / `medium` / `low`);
- `kind` (`secret` / `sensitive_file` / `dependency_audit` / `review`);
- `file` (`path:line` for secrets);
- `description`.

## Severity and blocking rules

| Finding | Severity |
|---|---|
| Sensitive file tracked | `critical` |
| Private key, `sk-` key, AWS access key id, GitHub, Google or Slack token (anywhere, including tests) | `critical` |
| Hard-coded credential in non-test code | `high` |
| Hard-coded credential in test code (`is_test_path`) | `medium` |
| Dependency audit failed | `medium` |
| Code-review security finding | as reviewed, **not blocking** |

The report passes when no finding has severity `critical` or `high`, ignoring findings
of kind `review`.

## LLM Profile

None.

## Allowed Tools

The workflow uses:

- the [git tool](../tools/git_tool.md) (`ls-files`);
- the [secret scan](../tools/secret_scan_tool.md);
- `DependencyAuditTool` from [`tools/project_commands.py`](../../src/software_factory/tools/project_commands.py) (see the [build tool](../tools/build_tool.md#how-the-build-phase-runs-commands)), running on the [shell tool](../tools/shell_tool.md) for audit commands, with the build-phase environment.

## Prompt Location

None.

## Workflow Stage

Node `security_validation`, lifecycle `SECURITY_VALIDATION`, between `code_review` and
`final_validation`.

```text
code_review → security_validation ─┬─ passed → final_validation
                                   ├─ blocking & security_rounds < max_review_rounds
                                   │     → BUG_FIXING → debug (Coding Agent, fix) → build → test
                                   │     → commit "fix(security): …" → security_validation
                                   └─ blocking & rounds used up → FAILED
```

## Preconditions

It is reached only from `CODE_REVIEW`, when all tasks are committed. It has no
quality-gate check of its own. The `release` gate later requires "security validation
passed".

## Postconditions

- The `security` state and `security.md` / `security.json` are written.
- A `WORKFLOW` execution record (stage `security_validation`, status success or failed, summary "Security validation passed/failed: N finding(s)") is appended to `execution-history.json`.
- **Passed:** `security_validation` is added to `completed_stages` and the lifecycle moves to `FINAL_VALIDATION`.
- **Blocking findings with rounds left:**
  - `security_rounds` goes up by 1;
  - `phase = "security"` and `debug_iterations = 0`;
  - `fix_context = {source: "security", findings: [blocking]}`;
  - lifecycle `BUG_FIXING`.

  The Coding Agent then receives, for example, `settings.py:1 … Possible hard-coded credential`.

## Approval Requirements

None at this step. The human reviews `security.md` at the release gate, and `release`
cannot run unless `security.passed` is true.

## Failure Conditions

- Blocking findings remain after `limits.max_review_rounds` fix rounds: `FAILED` with "N blocking security finding(s) remain after M fix round(s)" and the hint to remove the secrets by hand, then `factory retry`.
- A fix attempt itself can fail as described in [Coding Agent → Retry Behavior](coding_agent.md#retry-behavior).

## Retry Behavior

- Fix rounds are bounded by `limits.max_review_rounds`. Code review uses the same setting but keeps its own counter; this step counts in `security_rounds`.
- `factory retry` runs the scan again with fresh fix rounds (`debug_iterations`, `review_rounds` and `security_rounds` are reset by `development_nodes.prepare_retry`). Manual edits in `source/` are committed automatically first, so removing a secret by hand and retrying is enough.

## Dependencies

- [`tools/secret_scan.py`](../../src/software_factory/tools/secret_scan.py)
- [Git tool](../tools/git_tool.md) and [shell tool](../tools/shell_tool.md)
- [Code Review Agent](code_review_agent.md) (security findings)
- [Coding Agent](coding_agent.md) (fixes)

## Examples

A `settings.py` containing `ADMIN_PASSWORD = "hunter2hunter2"` produces a `high`
`secret` finding at `settings.py:1`. The Coding Agent rewrites it as
`os.environ.get("ADMIN_PASSWORD", "")`, the tests pass, `fix(security): …` is committed,
and the re-scan passes (`test_hardcoded_secret_blocks_until_removed`).

## Known Limitations

- Detection is pattern-based. There is no entropy analysis, no static analysis (SAST) and no configuration validation yet.
- Only tracked files are scanned. Files over 1 MB and non-UTF-8 files are skipped.
- A failed dependency audit is only `medium`, so it never blocks. Audit output is not parsed for severity.
- Audit commands usually need network access, and `security.network_enabled` is not enforced at OS level.
- Placeholder detection (`example`, `changeme`, `dummy`, ...) can hide a real secret that happens to contain those words.
