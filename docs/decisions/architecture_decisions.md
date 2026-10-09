# Architecture decisions

Short records of decisions made while implementing the specification
(`software_factory_specification_final.md`), including every deliberate
deviation from it. Newest decisions are appended at the end.

---

## ADR-001 — Python 3.11 with uv

**Decision:** Python 3.11 (pinned in `.python-version`), dependencies and the
virtual environment managed with `uv` (`pyproject.toml` + `uv.lock`).
**Why:** LangGraph requires Python ≥ 3.10; 3.11 was already installed through
uv on the development machine. `uv sync` gives a reproducible environment in
one command (spec §68.4).

## ADR-002 — LangGraph `interrupt()` + per-project SQLite checkpointer

**Decision:** Human gates call LangGraph's `interrupt()`; Factory Core resumes
them with `Command(resume=...)`. Each project has its own checkpoint database
(`projects/<id>/checkpoints/workflow-checkpoints.sqlite`) and the LangGraph
thread id is the project id.
**Why:** Checkpoints are written after every node, so a process can exit at a
gate — or be killed mid-node — and `factory continue` resumes from the last
completed node with no chat history (spec §33). Separate files make project
isolation physical, not just logical (spec §2.7).

## ADR-003 — Lifecycle status is the only routing key

**Decision:** Every conditional edge uses one router,
`route_by_lifecycle`, which maps `lifecycle_status` to the next node. Nodes set
the status only through `domain.lifecycle.transition()`, which rejects any
transition not in `ALLOWED_TRANSITIONS`.
**Why:** Keeps the state machine explicit and auditable (spec §54) and makes
it impossible for an LLM to steer the workflow: agents *propose*
(e.g. clarification questions), deterministic node code *decides* (spec §55).

## ADR-004 — Deterministic clarification rule

**Decision:** The workflow waits for the user iff the Requirement Agent
proposed at least one question marked `required` **and** fewer than
`limits.max_clarification_rounds` rounds have run. Question ids (`Q1`, `Q2`,
…) are assigned by the factory and continue across rounds. Optional or
over-the-limit questions become `open_questions` in `requirements.md` for the
human reviewer.
**Why:** The agent must not silently invent requirements (spec §2.6), but the
factory must never loop unboundedly (spec §41).

## ADR-005 — Ollama native API rather than its OpenAI-compatible endpoint

**Decision:** The Ollama adapter uses `/api/chat` with `format` set to the
JSON schema of the expected output, `/api/tags` for health and installed
models, and `/api/show` for model metadata.
**Why:** The native API supports schema-constrained output and exposes the
metadata `factory doctor` needs (parameter size, quantization, context length).
The factory's own `LLMRequest`/`LLMResponse` remain the domain contract
(spec §68.3), so an OpenAI-compatible adapter can be added later as a separate
provider type without touching agents.

## ADR-006 — Model profiles with capability-filtered fallback

**Decision:** Agents request a logical profile (`reasoning`, `coding`, …).
The `ModelRouter` resolves it to `preferred_model` + `fallback_models`,
dropping any candidate that lacks a required capability. Retryable errors
(timeout, rate limit, provider unavailable, network, malformed response) are
retried per model; after that, and for model-unavailable / authentication /
context-length / invalid-output errors, the next compatible model is tried.
**Why:** Spec §21, §26, §48: fallback must be capability-compatible, and model
names live only in configuration (a test asserts no model name appears in the
source).

## ADR-007 — Structured output validated in the router, with bounded repair

**Decision:** `generate_structured` sends the Pydantic JSON schema to the
provider and in the prompt, validates the reply, and on failure sends the
validation errors back for at most `limits.max_structured_output_repairs`
repair attempts before failing with `invalid_structured_output`.
**Why:** Workflow decisions must come from validated schemas, never from free
text (spec §29); local models occasionally miss fields, and a bounded repair
loop recovers most such cases cheaply.

## ADR-008 — Persistence split: registry, checkpoints, artifacts

**Decision:**
- `<workspace>/factory.db` — SQLite registry of projects and headline status.
- `projects/<id>/checkpoints/` — LangGraph checkpoints (**source of truth** for
  workflow state).
- `projects/<id>/*.md|*.json` — human-readable artifacts. `state.json`,
  `progress.md`, `decisions.md`, `errors.md` are mirrors rewritten after every
  node; stage outputs keep every revision under `artifacts/<stem>.rev<N>.json`.
**Why:** Spec §2.8 / §30: humans read Markdown, machines read JSON/DB. Writes
are atomic (temp file + rename) and pass through secret redaction.

## ADR-009 — Implementation planning and plan approval are part of the first slice

**Deviation:** The MVP slice in spec §50/§63 stops after technology approval;
spec §2.5 lists the implementation plan as a minimum approval gate. The first
slice includes the Implementation Planning Agent and the plan gate, so all four
minimum gates exist.
**Why:** It is the same agent pattern at negligible extra cost, and it gives
the build phase an approved, traceable task list to start from.

## ADR-010 — New status `READY_FOR_DEVELOPMENT` (superseded by ADR-020)

**Deviation:** Not in the spec's example state list (§54). After plan approval
the design-phase graph ends in `READY_FOR_DEVELOPMENT`; its only allowed
transition is to `DEVELOPMENT`. The build/verify states are defined in the
enum and transition table but no node reaches them yet.
**Why:** Makes it explicit and honest that the design phase is complete and
the development phase is not implemented.

## ADR-011 — Repository analysis runs after requirement approval

**Decision:** `analyze_repository` is a deterministic node (no LLM) that runs
after requirements are approved. For greenfield projects it records a skip;
for existing projects (`factory create --repository <path>`) it collects
languages, frameworks, package managers, build systems, tests, config files,
entry points and git state, and passes them to later agents.
**Why:** Matches the spec's graph (§7) and the AI/deterministic split (§2.4,
§38): raw repository facts come from tools.

## ADR-012 — Design-phase agents get no tools

**Decision:** Requirement, Architecture, Technology Selection and
Implementation Planning agents declare `allowed_tools = ()`. The tool layer
(filesystem, shell, git, build/test/lint/format/package commands) is built and
tested, and `ToolKit` hands an agent only the tools it declares.
**Why:** Least privilege (spec §18, §44). The tools exist now because the
spec's test list (§52) covers filesystem safety, git, command restrictions
and timeouts, and the build phase needs them next.

## ADR-013 — Pause is a flag checked between nodes

**Decision:** `factory pause` sets a flag in the registry. A run in progress
(possibly in another terminal) checks it after every node and stops; the
checkpoint is already saved. `factory continue` clears the flag. An `fcntl`
lock ensures only one process advances a project at a time.
**Why:** Pausing never interrupts a node halfway, and concurrent runs cannot
corrupt a project's state.

## ADR-014 — Redact everything that is written

**Decision:** All artifact writes and log records go through
`observability.redaction.redact`, which removes known key formats
(`sk-…`, AWS, GitHub, Google, bearer tokens, `password=`-style pairs, PEM
private keys) and the values of any environment variable whose name contains
KEY / TOKEN / SECRET / PASSWORD / CREDENTIAL. Shell tools also drop such variables from
child-process environments.
**Why:** Spec §25, §43: secrets must never reach artifacts, history or logs,
even when a provider echoes them in an error message (covered by a test).

## ADR-015 — Additional CLI commands

**Decision:** Besides the commands in spec §5.1, the CLI adds `list`,
`requirements` (set requirements before start), `cancel` (abort a failed
project), `doctor` (spec §68.6) and `version`.
**Why:** Needed for the first-run experience (spec §68.19) and for a failed
project to reach a terminal state.

## ADR-016 — `.env` is loaded, never overriding the shell

**Decision:** `load_config()` reads `./.env` (current directory) and sets only
variables that are not already set. Keys are read by a provider only when that
provider is used.
**Why:** Spec §47 allows `.env` for local development; the real environment
must stay authoritative.

## ADR-017 — `doctor` severity follows what the workflow actually needs

**Decision:** A missing model is `FAIL` only when it is the preferred model of a
profile the implemented agents use (`reasoning`); other missing models are
`WARN`.
**Why:** A new developer should need only one pulled model to start; the
`coding` profile is configured for the build phase but unused so far.

## ADR-018 — Technology selection is a list of categorized choices

**Deviation:** Spec §12 sketches an object keyed by category
(`frontend`, `backend`, …). The schema is instead a list of
`{category, choice, rationale, alternatives_considered, requirement_ids}` plus
`not_applicable`.
**Why:** Projects need different categories (a CLI tool has no frontend; a
data pipeline may need a scheduler). A fixed key set would nudge the model
toward a universal stack, which spec §12 forbids.

## ADR-019 — Agents propose code; the workflow applies and proves it

**Decision:** The Coding Agent returns `CodeChanges` (full file contents,
or a change request). The workflow validates paths and writes the files with
`FilesystemTool`, then runs the plan's own commands through `ShellTool`.
Code is committed (`T-n: title`) only after the test command exits 0.
**Why:** Spec §40, §68.12: tool results are ground truth; an agent can never
mark a build or test as passed. Keeping writes in the workflow also keeps
agents free of side effects (spec §68.11).

## ADR-020 — The build phase follows plan approval automatically

**Decision:** `READY_FOR_DEVELOPMENT` is no longer terminal; the router sends
it to `prepare_development`. Projects that ended there under workflow
`design-phase-1.0` resume with `factory continue` (the graph is re-opened
after `plan_approval`). Plans approved before `commands` existed are sent back
to planning with feedback.
**Why:** Plan approval is the human decision that authorises development;
another gate would add no information.

## ADR-021 — Project commands are part of the approved plan

**Decision:** The plan carries `commands` (setup, format, lint, build,
**test** (required), package, audit) as argv lists with a working directory —
no shell syntax. Before development the factory checks each program is in
`security.allowed_commands` and installed, and fails early otherwise. The
technology and planning agents are told which programs are installed.
**Why:** The human approves exactly what will run; no stack is assumed.

## ADR-022 — Bounded, typed fix loops

**Decision:** One `debug` node serves every fix: failed build/test
(Testing Agent analysis first), review findings, security findings, final
validation failures and release rejections. `development.phase` records where
a green fix returns to. Limits: `max_debug_iterations` per failure,
`max_review_rounds` for review and security. A Testing Agent verdict of
`environment` stops immediately for a human. A human `retry` restores the
fix budget.
**Why:** Spec §41: never an infinite autonomous loop, and never waste fix
attempts on problems code cannot fix.

## ADR-023 — Security validation is deterministic

**Decision:** No LLM security agent. The step scans tracked files for secret
patterns (literal values only) and sensitive files, and runs the plan's
`audit` commands if any. Critical/high deterministic findings block (fix
loop, then FAILED); code-review security findings are listed for information.
**Why:** Spec §17: AI review must not replace deterministic tools. The Code
Review Agent already covers LLM-assisted security review.

## ADR-024 — Existing repositories are changed on a branch

**Decision:** For `--repository` projects the factory requires a clean git
repository and works on `factory/<project-id>`; other branches are untouched.
**Why:** Spec §39: no destructive git operations; the human merges.

## ADR-025 — Only fresh code in tests: no Python bytecode cache

**Decision:** Project commands run with `CI=true`, colour off and
`PYTHONDONTWRITEBYTECODE=1`.
**Why:** Found in testing: a fix written within the same second and with the
same size as the broken file reused a stale `.pyc`, making a correct fix look
like a failure.

## ADR-026 — One OpenAI-compatible adapter covers other engines and cloud APIs

**Decision:** A second provider type, `openai_compatible`, speaks the Chat
Completions format (`base_url`, optional `api_key_env`, `structured_output`
= `json_schema` | `json_object` | `prompt`). Verified live against Ollama's
`/v1` endpoint.
**Why:** One adapter serves LM Studio, llama.cpp, vLLM, DeepSeek, OpenAI and
others, so switching from a local model to an API is configuration only
(spec §2.3, §68.2). The factory's own request/response types remain the
contract (§68.3); the router still validates every structured answer.

## ADR-027 — The spec's tool classes are the execution path

**Decision:** Each plan step runs through its named tool (`PackageManagerTool`,
`FormatterTool`, `LintTool`, `BuildTool`, `TestTool`, `DependencyAuditTool`),
and the Coding Agent's files are written only through a `FilesystemTool`
obtained with `ToolKit.require(..., agent.allowed_tools)`.
**Why:** Spec §18: explicit, permission-controlled tools; an agent that does
not declare a tool can never have it used on its behalf.

## ADR-028 — Planned test files must exist before tests can pass

**Decision:** Before the test command runs for a task, the factory checks that
the task's planned test files exist; missing ones are a failed verification
that goes through the normal fix loop. The Coding Agent's output has a
separate `test_code` field for tests.
**Why:** Found live: `qwen2.5:14b` skipped tests when asked in prose, and a
test command can exit 0 with zero tests. A required output field fixed the
model behaviour (3/3 live passes); the file check makes "no tests" impossible
to pass regardless of model.

## ADR-029 — Resume inspects the repository; releases are exact

**Decision:** `factory continue` compares the generated repository with the
last commit the factory made and reports (and records) outside changes.
Approving a release is refused if the code differs from the commit that
passed final validation; rejecting the release re-validates it.
**Why:** Spec §33 (validate external changes on resume) and §68.15 (the
validated artifact is the released artifact).

## ADR-030 — Model evaluation uses the real agents

**Decision:** `factory evaluate` runs the actual factory agents and prompts on
five small fixed tasks and checks the answers with code; the coding case runs
the generated tests for real.
**Why:** Spec §68.17 asks whether a model is capable enough *for this factory*,
not for a general benchmark. Using the real agents measures exactly that.

## ADR-031 — Reproducibility records

**Decision:** Every history record carries the config fingerprint; the build
phase records the versions of git and every program the plan uses.
**Why:** Spec §68.16. Factory, workflow, prompt, agent and model versions were
already recorded.

## ADR-032 — Impact analysis before approving changes

**Decision:** An Impact Analysis Agent (`reasoning` profile, no tools) runs
(1) after the deterministic repository analysis of an existing project, and its
result feeds the Architecture and Planning agents (planning must cover the
regression areas with tests); (2) when the Coding Agent raises a change
request, before the human decides — rendered in `change-request.md` next to the
deterministic facts. If it fails during a change request, the request still
reaches the human and the error is recorded.
**Why:** Spec §36 (change request → impact analysis → revision → approval) and
§68.7 (existing software: new requirement → impact analysis → architecture
change → implementation → regression testing). Regression testing is the
final validation's full test-suite run, which includes the pre-existing tests.

## ADR-033 — Project commands run without the factory's own Python environment

**Decision:** Every project command (setup, format, lint, build, test,
package) runs with `tools/shell.py` `project_environment()`: the factory's
virtual-environment `bin` directory is removed from `PATH`, and `VIRTUAL_ENV`
and `PYTHONHOME` are unset. The "installed commands" given to the Technology
Selection and Planning agents and the plan's "not installed" check use the same
lookup (`find_program()`). Controlled by `security.isolate_project_environment`
(default `true`).
**Why:** Found in the first live build: the factory's venv made `python` and
`pytest` look installed, so a generated app silently depended on the factory's
own tools.

## ADR-034 — A greenfield project is always its own git repository

**Decision:** The greenfield `source/` folder gets `git init` when
`source/.git` is missing (not only when git says "not a repository"), and the
factory then checks that `git rev-parse --show-toplevel` is `source/` itself;
otherwise the build stops.
**Why:** Found live: the workspace sits inside the factory's own git checkout,
so git commands found the *factory's* repository and commits went there.

## ADR-035 — Files written by the test run are side effects, not changes

**Decision:** Final validation lists uncommitted changes *before* the final
run (these fail validation). Files that the run itself creates or changes are
stored as `validation.test_side_effects` and reported as a warning. The release
guard and the resume external-change check ignore exactly those files
(`GitTool.changed_paths()`).
**Why:** A test that writes a data file into the repository made the release
guard refuse a release that nobody had changed. The warning keeps the problem
visible (tests should use temporary folders) without blocking.

## ADR-036 — Targeted edits and one correction attempt for invalid answers

**Decision:** The Coding Agent may return `edits` (`path`, `find`, `replace`);
`find` must occur exactly once, otherwise the change is refused with
`ChangeApplyError`; after such a failure the rest of the fix round must send
complete files (`allow_edits: false`), because small models repeated the same
failing edit. A path given twice in one answer keeps the last copy with a
warning. When an agent's deterministic `validate_output` check raises
`AgentOutputError`, the model gets `validation_repairs` (1) correction attempt
with the exact reason.
**Why:** Small local models rewrote whole files for one-line fixes (and broke
working code), and answers were rejected for mistakes the model could fix when
told what was wrong.

## ADR-037 — `factory retry --hint` lets a human steer a failed stage

**Decision:** `factory retry <project> --hint "…"` stores the hint as
`development.human_hint` in the build phase (passed to the Testing and Coding
agents as `human_guidance` and cleared when the failing work passes), or as
`revision_feedback` for a failed design stage. History records "with human
hint". A hint when nothing has failed is a `WorkflowActionError`.
**Why:** The live run showed that a one-line human diagnosis ("JSON keys come
back as strings") unblocks a small model that is otherwise stuck.

## ADR-038 — Stuck detection and failed-attempt memory

**Decision:** Each build/test failure gets a signature (its error lines, digits
ignored). A repeated failure adds a NO PROGRESS note to the fix details; after
`NO_PROGRESS_LIMIT` = 3 identical failures the stage fails with "No progress:
…" and a `--hint` suggestion. Fixes that did not help are kept in
`development.fix_attempts` and shown to the next attempt as "EARLIER FIX
ATTEMPTS FOR THIS FAILURE"; the memory is cleared when the tests pass.
**Why:** Without it the model repeated the same wrong fix until the debug budget
ran out, with no useful signal for the human.

## ADR-039 — Several fix candidates, each really checked

**Decision:** In a bug-fixing round for a build/test failure the Coding Agent
proposes up to `limits.fix_candidates` (default 3) fixes one after another.
Each is applied and checked with the real commands (setup if manifests changed,
format, lint, build, test, plus the planned-test-file check); the first that
passes is kept, failing ones are rolled back and described to the next
candidate. The last candidate is kept even if it fails, so the normal loop
continues from it. Review, security, validation and release-feedback fixes use
one candidate.
**Why:** Local models are inconsistent but free to call again; trying a few
answers and keeping the one the real tests accept is cheaper than more rounds.

## ADR-040 — Two local models: a reasoning model and a coder model

**Decision (user, 2026-09-30):** `local_model` (`qwen2.5:14b`) serves the
`reasoning` profile; `local_coder` (`qwen2.5-coder:14b`, `num_ctx` 32768)
serves the `coding` profile with `local_model` as fallback and temperature
0.2. Ollama runs with `OLLAMA_MAX_LOADED_MODELS=1` so only one model is in
memory (measured ~15 GB for a 14B model at 32k context, ~12 GB at 16k).
**Why:** In the first live build `qwen2.5:14b` alone needed human hints for
logic bugs; the coder model passes `factory evaluate --case code_generation`.
The small temperature gives fix candidates some variety.

## ADR-041 — A local web UI as a second front end over Factory Core

**Decision:** `factory ui` starts a FastAPI app with server-rendered Jinja2
pages and a few lines of plain JavaScript (status polling). Every page action
maps to one `FactoryService` method — the same facade the CLI uses — and long
actions run in a background worker (`JobRunner`, one per project). It binds to
`127.0.0.1`, protects forms with a per-process token, renders model-written
Markdown with raw HTML disabled, and serves only whitelisted documents.
**Why:** Non-technical users should not need the terminal (the spec's
"CLI / API / future UI"). Pure Python keeps the factory free of Node/React
(user requirement) and keeps workflow logic in one place; an import-boundary
test stops the web layer from reaching past Factory Core.

## ADR-042 — Projects are deleted through the factory; missing folders are reported

**Decision:** `factory delete` / the web **Delete project** button removes the
register row and the project's workspace folder together, under the project's
run lock. A registered project whose folder was removed by hand is shown as
"Folder missing" and refuses every action instead of recreating an empty
folder. Existing-code projects must point at the top folder of a git
repository, and deleting such a project never touches that repository.
**Why:** Deleting a folder by hand left a ghost entry in the list, and opening
it silently recreated the folder. A project pointed at a folder holding many
repositories (e.g. `~/Documents/GitHub`) would analyse and change the wrong
code.

## ADR-043 — A released app is updated as the next version of the same project

**Decision:** `factory update` / **Update this app** re-opens a `COMPLETED`
project (`COMPLETED → REQUIREMENTS_ANALYSIS`) instead of requiring a new
existing-code project. The finished version's documents move to
`versions/v<N>/`; per-version state is reset; the design agents receive an
`update_request` next to the previously approved artifacts; the released code
is analysed with impact analysis; only the new tasks are built; the release
takes the next `release-N` tag.
**Why:** Users expect to keep changing an app they built, with its history in
one place. Revising the approved artifacts (rather than starting over) keeps
unchanged requirements and design stable, and re-approving every gate keeps the
human in control of each version.
