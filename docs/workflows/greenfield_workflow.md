# Greenfield Workflow

Building new software from an idea (spec §37, §68.7). This is a walkthrough
of a greenfield project from requirements to a tagged release; the
authoritative node/transition reference is
[the execution flow](software_factory_execution_flow.md).

## Flow

```text
Idea / requirements
  ▼
New project                 factory create
  ▼
Requirements (+ clarify)    Requirement Agent + approval
  ▼
Architecture                Architecture Agent + approval
  ▼
Technology                  Technology Selection Agent + approval
  ▼
Plan (tasks + commands)     Implementation Planning Agent + approval
  ▼
Create repository           projects/<id>/source/: git init, .gitignore, initial commit
  ▼
Per task: code → build → test → commit      (debug loop on failure)
  ▼
Code review → security validation → final validation
  ▼
Release approval            human
  ▼
Release                     git tag release-N, release-notes.md → COMPLETED
```

## Walkthrough

Prerequisites: [setup guide](../setup/setup_guide.md), a running local
model ([local model setup](../models/local_model_setup.md)), a passing
`factory doctor`, and the programs the plan will use (e.g. `python`,
`npm`) installed and listed in `security.allowed_commands`.

```bash
# 1. Create the project (mode: greenfield because no --repository)
factory create "Employee Manager" --requirements-file examples/employee-management/requirements.md
#    → project id "employee-manager", status CREATED

# 2. Start, clarify, approve the design stages
factory start employee-manager
factory questions employee-manager
factory answer employee-manager Q1 "Around 200 employees, single company"
factory continue employee-manager
factory approve employee-manager            # requirements
factory reject employee-manager --reason "Use a modular monolith, not microservices"
factory approve employee-manager            # architecture (revision 2)
factory approve employee-manager            # technology

# 3. Review implementation-plan.md: tasks, the "Commands the factory will run"
#    table (setup/lint/build/test/package/audit) and the "Environment check".
#    If a program is not allowed or not installed, the CLI prints
#    "⚠ The plan's commands cannot run on this machine" at this prompt:
#    reject the plan with what to change, or install the program first.
factory approve employee-manager            # plan
#    → prepare_development → every task implemented, built, tested, committed
#    → code review → security → final validation
#    → WAITING_FOR_RELEASE_APPROVAL  (this one command can run for a long time)

# 4. Review validation.md, review.md, security.md, and the code in source/
factory reject employee-manager --stage release --reason "Add a README"
#    → fix → build → test → commit → review again → … → release gate again
factory approve employee-manager --stage release
#    → git tag release-1, release-notes.md → COMPLETED
#    Refused if source/ changed after final validation (HEAD moved or
#    uncommitted edits): "The code changed after final validation, so it
#    cannot be released as is". Reject the release instead; the fix cycle
#    commits your edits and re-runs build/test/review/security/validation.
```

At any point: `factory status <id>` (shows `Tasks: 3/7 done · next: T-4 ·
code: …/source`), `factory history <id>`, `factory pause <id>` /
`factory continue <id>`. If you edited `source/` in between, `factory
continue` reports "External changes detected in …" (HEAD moved and/or
uncommitted files) and records it in history; the changes are not blocked
and go through the next build, test and review. If a stage fails (model server down, a program not
installed, fix budget exhausted, a secret the factory could not remove): fix
the cause, then `factory retry <id>`, or `factory cancel <id>`.

If the Coding Agent decides an approved decision cannot work, the project
stops at `WAITING_FOR_CHANGE_REQUEST_APPROVAL`; see
[change-request workflow](change_request_workflow.md).

## What the project directory contains at the end

`factory-workspace/projects/employee-manager/`:

- design: `requirements-input.md`, `requirements.md/.json`,
  `architecture.md/.json`, `technology-stack.md/.json`,
  `implementation-plan.md/.json`;
- build: `source/` (the generated git repository), `test-results.md/.json`,
  `test-analysis.md/.json` (if a failure was analyzed), `review.md/.json`,
  `security.md/.json`, `validation.md/.json`, `release-notes.md/.json`,
  `change-request.md/.json` (if one was raised);
- bookkeeping: `project.md`, `decisions.md`, `progress.md`, `errors.md`,
  `clarification-questions.md`, `state.json`, `execution-history.json`,
  `artifacts/*.revN.json`, `artifacts/tool-runs/NNN-<step>.log`,
  `checkpoints/`.

There is no `repository-analysis.*` for greenfield projects. Layout:
[persistence](../architecture/persistence_architecture.md#workspace-layout).

## Greenfield specifics

- `analyze_repository` still runs but only records
  "Greenfield project: repository analysis skipped" and sets `repository`
  to `null`; agents receive `repository_analysis = None.`
- The Technology Selection Agent chooses the stack from requirements,
  architecture and the build machine's `environment` (allowed and installed
  commands); there is no default stack.
- `prepare_development` creates `projects/<id>/source/`, runs `git init`,
  writes a default `.gitignore` (dependencies, build output, `.env`, logs)
  and commits `chore: initialize repository (Software Factory)`. Commits use
  your git identity, or `Software Factory <software-factory@localhost>` if
  none is configured. Nothing is pushed.
- Each task becomes one commit `<task id>: <title>`; fixes after review,
  security, validation or release feedback are `fix(<phase>): …` commits.
  A task whose planned test files (path-like entries of its `tests`) were
  not written cannot pass: the test step fails with "The task's planned
  tests were not written" and goes through the fix loop.
- `prepare_development` records the versions of `git` and every plan
  program (`development.tool_versions`, also in `execution-history.json`).
- The plan names the release artifact (spec §68.15); final validation runs
  the plan's `package` commands and records the artifact name in
  `validation.md` and `release-notes.md`.

## Not implemented

- Pushing to a remote, deployment, container images: **Planned — not
  implemented yet.**
- Running generated code in a sandbox: commands run on the host, inside the
  project folder ([security](../architecture/security_architecture.md)).
