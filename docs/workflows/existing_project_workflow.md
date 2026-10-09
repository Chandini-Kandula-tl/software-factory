# Existing-Project Workflow

Working on software that already exists (spec §37, §38, §68.7). Existing
projects run **the same graph** as greenfield projects. The differences:
`analyze_repository` produces real repository facts that later agents
receive, and the build phase changes your repository on a dedicated branch
instead of creating a new one. Node and transition reference:
[the execution flow](software_factory_execution_flow.md).

## Flow

```text
Spec §68.7 target                         Implemented
─────────────────────────────────────     ──────────────────────────────────────────
Existing repository                       factory create … --repository <path>
  ▼                                          ▼
Repository analysis                       requirements (+ clarification) + approval
  ▼                                          ▼
Understand current system                 analyze_repository (deterministic facts)
  ▼                                          ▼
New requirement / change request          Impact Analysis Agent (mode existing_project)
  ▼                                          → impact-analysis.md/json
Impact analysis                              ▼
  ▼                                       architecture (sees facts + impact) + approval
Architecture change                          ▼
  ▼                                       technology (sees facts) + approval
                                             ▼
                                          implementation plan (sees facts + impact;
                                            tests for the regression areas) + approval
                                             ▼
Implementation                            branch factory/<project-id>; per task:
  ▼                                         code → build → test → commit
Regression testing                        full test suite (incl. pre-existing tests)
  ▼                                         after every change and in final validation
Review                                    code review → security → final validation
                                            → release approval → tag release-N
```

Note the ordering: repository analysis runs **after** requirements approval,
not before requirements. The Requirement Agent is told the project mode
(`project_mode = existing`) but does not see repository facts.

## Walkthrough

```bash
cd ~/code/legacy-hr && git status          # must be clean: commit or stash first
factory create "Legacy HR" --repository ~/code/legacy-hr \
    --requirements "Add an audit log for every change to employee records."
#   → mode "existing"; the path must be an existing directory (stored resolved)

factory start legacy-hr             # requirements → clarification or approval gate
factory approve legacy-hr           # → analyze_repository → design_architecture
factory approve legacy-hr           # architecture
factory approve legacy-hr           # technology
factory approve legacy-hr           # plan → build phase on branch factory/legacy-hr
#   → WAITING_FOR_RELEASE_APPROVAL
factory approve legacy-hr --stage release   # → tag release-1 → COMPLETED
```

Afterwards the repository is left checked out on `factory/legacy-hr`. Review
the branch (`git log main..factory/legacy-hr`) and merge it yourself; the
factory never pushes or merges.

Rejection, clarification, change requests, pause, retry and cancel behave
exactly as in the [greenfield workflow](greenfield_workflow.md).

## Repository rules in the build phase

Checked by `prepare_development` (`development_nodes._prepare_repository`):

| Rule | If violated |
|---|---|
| The path is a git repository | `FAILED`: "`<path>` is not a git repository" |
| The working tree is clean (no uncommitted changes) | `FAILED`: "has uncommitted changes" — commit or stash, then `factory retry` |
| Work happens on branch `factory/<project-id>` | created from the current `HEAD` if missing, else checked out |
| The branch you started from (e.g. `main`) | untouched: every commit goes to `factory/<project-id>` |

`base_commit` (HEAD when development started) is recorded; code review sees
the diff `base_commit..HEAD`, i.e. only the factory's changes. Final
validation requires the working tree to be clean again, and `release` tags
`release-N` on the factory branch.

Because the code lives outside the project directory, the Coding Agent's
writes are confined to the repository root (`FilesystemTool`), and commands
run with the repository (or a `cwd` below it) as working directory.

## Changes made outside the factory

- **On resume** (`factory continue`, spec §33): `FactoryService._check_external_changes`
  compares the repository's HEAD with `development.last_commit` (the last
  commit the factory made or recorded) and counts uncommitted files. If
  either differs, the status note says "External changes detected in
  `<path>`: HEAD moved from … to …; N uncommitted file(s). They will be
  built, tested and reviewed with the next step." and a `workflow` history
  record (stage `resume`) is written. The changes are not blocked.
- **At the release gate:** `factory approve --stage release` is refused
  ("The code changed after final validation, so it cannot be released as
  is") if HEAD differs from the commit final validation passed
  (`validation.head_commit`) or there are uncommitted changes other than the
  files the final test run itself wrote (`validation.test_side_effects`). Reject the release
  instead (e.g. `--reason "re-validate my manual changes"`): the fix cycle
  commits the edits (`add -A`) and build, test, review, security and final
  validation run again.

## Repository analysis

Implemented in `tools/repository_analysis.py`
([repository analysis tool](../tools/repository_analysis_tool.md)). It is
deterministic — no LLM — and read-only.

| Fact | How it is found |
|---|---|
| `languages` (language → file count) | file suffixes (`.py`, `.ts`, `.go`, `.java`, …) |
| `dependency_files`, `package_managers` | known manifest/lock files (e.g. `pyproject.toml`, `package.json`) |
| `frameworks` | dependency names inside manifests (e.g. `fastapi` → FastAPI) |
| `build_systems`, `configuration_files`, `test_locations`, `likely_entry_points` | well-known file and directory names |
| `top_level_structure` | top-level entries of the repository |
| `total_files_scanned` | capped at 20 000 files; skips `.git`, `node_modules`, virtualenvs, build output and tool caches |
| `git` | `GitTool.state()`: is repository, branch, commit, clean working tree, changed-file count |

Output: `repository` in state, `repository-analysis.json/.md`,
`artifacts/repository-analysis.rev1.json`, and a `tool` execution record.
An `OSError` or `FactoryError` during analysis → `FAILED`.

| Agent | Receives `repository_analysis` | Receives `impact_analysis` |
|---|---|---|
| Requirement Agent | no (gets `project_mode`) | no |
| Impact Analysis Agent | yes | — (produces it) |
| Architecture Agent, Implementation Planning Agent | yes | yes |
| Technology Selection Agent | yes | no |
| Coding, Testing, Code Review Agents | no — they see the actual file tree and file contents instead | no |

## Impact analysis

Right after the deterministic analysis, still inside `analyze_repository`,
the [Impact Analysis Agent](../agents/impact_analysis_agent.md) runs in mode
`existing_project` with the approved requirements and the repository
analysis (spec §68.7). It reports which requirements, components and files
the new requirement touches, the existing behaviour that must be
regression-tested (`regression_areas`), the risks and a recommendation.

- Output: `impact_analysis` in state, `impact-analysis.md/json` (latest only),
  and an `agent` execution record (stage `repository_analysis`).
- The Architecture Agent and the Implementation Planning Agent receive it as
  the optional input `impact_analysis`.
- If the agent fails, the project goes to `FAILED` at `repository_analysis`;
  `factory retry` re-runs the repository analysis and the agent.
- Greenfield projects skip it.

## Regression testing

There is no separate regression-testing stage. Regression testing works in
two steps:

1. **Planned:** the impact analysis `regression_areas` are passed to the
   Implementation Planning Agent, whose task prompt asks it to plan tests
   covering them, so the existing behaviour the change touches is protected
   by tests in the plan.
2. **Run:** the plan's `test` command runs the **whole** test suite of the
   repository — including the pre-existing tests — after every task and
   fix, and again in final validation. A pre-existing test that breaks
   fails the step and goes through the normal fix cycle.

## Safety

- The design phase never writes to the repository.
- The build phase writes only on `factory/<project-id>`; destructive git
  operations (`push`, `reset`, `clean`, `rebase`, force flags, discarding
  checkouts) are refused by `GitTool`.
- Commands run on the host with your user's permissions; only the approved
  plan commands run. See
  [security architecture](../architecture/security_architecture.md).

## Planned — not implemented yet

- Analysing the repository **before** requirements, so the Requirement
  Agent and clarification questions can use it.
- Current-architecture reconstruction as a separate artifact.
- Re-running repository analysis on resume. External changes are detected
  and reported (above), but the design artifacts are not updated.
