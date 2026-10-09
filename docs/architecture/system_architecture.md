# System Architecture

This document describes how the Software Factory is actually built today
(factory `0.2.0`, workflow `full-lifecycle-1.0`). Where the specification
describes something that does not exist yet, it is labelled
**Planned — not implemented yet**.

Related: [LLM architecture](llm_architecture.md) ·
[Orchestration](orchestration_architecture.md) ·
[Persistence](persistence_architecture.md) ·
[Security](security_architecture.md) ·
[Project structure](project_structure.md) ·
[Execution flow](../workflows/software_factory_execution_flow.md) ·
[Architecture decisions](../decisions/architecture_decisions.md)

## Scope of what is implemented

| Area | Status |
|---|---|
| Design phase: requirements → clarification → architecture → technology → implementation plan, each with a human approval gate | Implemented |
| Deterministic repository analysis for existing projects | Implemented |
| Impact analysis (Impact Analysis Agent): existing projects after repository analysis, and change requests before the human decides | Implemented |
| Build phase: per-task code → build → test → commit, bounded debug loop, code review loop, deterministic security validation, final validation with traceability, release approval gate, release (git tag) | Implemented |
| Change requests raised by the Coding Agent (approve reopens a design stage; reject continues) | Implemented |
| Pause / resume / retry / cancel, restart-safe checkpoints | Implemented |
| Resume check for external changes to the generated repository (spec §33): reported and recorded, not blocked | Implemented |
| Release guard: only the exact commit that passed final validation can be approved for release | Implemented |
| Plan environment check (commands' programs allowed and installed) shown before plan approval and enforced before development | Implemented |
| Reproducibility records: tool versions per build, config fingerprint on every execution record | Implemented |
| `factory evaluate`: checks the configured model on small factory tasks (spec §68.17) | Implemented |
| Local model provider (Ollama, provider type `ollama`) | Implemented |
| OpenAI-compatible provider (provider type `openai_compatible`: LM Studio, llama.cpp server, vLLM, DeepSeek, OpenAI, …) | Implemented |
| Human-initiated change requests | **Planned — not implemented yet** |
| Sandboxed (container) execution of generated code | **Planned — not implemented yet**; commands run on the host inside the project folder |
| Custom / reusable workflow definitions (spec §68.8) | **Planned — not implemented yet** |

A successful run ends at lifecycle status `COMPLETED` with a `release-N` git
tag in the generated (greenfield) or existing repository.

## Layers

```text
┌──────────────────────────────────────────────────────────────────┐
│ cli/            `factory` command (Typer). Parses args, prints.   │
│ web/            `factory ui`: local browser pages (FastAPI).      │
│                 Neither holds workflow logic.                     │
└───────────────────────────────┬──────────────────────────────────┘
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│ core/           FactoryService facade → ProjectService,           │
│                 WorkflowService, ApprovalService,                 │
│                 ClarificationService, ProjectRecorder, doctor,    │
│                 model_evaluation (`factory evaluate`)             │
└───────┬─────────────────────────┬───────────────────────┬────────┘
        ▼                         ▼                       ▼
┌────────────────────┐  ┌───────────────────────┐  ┌───────────────┐
│ orchestration/     │  │ persistence/          │  │ tools/        │
│ LangGraph graph,   │  │ factory.db registry,  │  │ repository    │
│ nodes, router,     │  │ per-project SQLite    │  │ analysis, fs, │
│ FactoryState,      │  │ checkpoints, artifact │  │ git, shell,   │
│ ports (recorder)   │  │ store, Markdown       │  │ secret scan,  │
│                    │  │                       │  │ source files, │
│                    │  │                       │  │ project cmds, │
│                    │  │                       │  │ ToolKit       │
└───┬──────────┬─────┘  └───────────────────────┘  └───────▲───────┘
    │          └──── build-phase nodes run tools ──────────┘
    ▼
┌────────────────────┐
│ agents/            │  design: Requirement, Architecture, Technology
│ LLMAgent contract  │  Selection, Implementation Planning, Impact
│                    │  Analysis ("reasoning")
│                    │  build: Coding, Testing, Code Review ("coding")
└─────────┬──────────┘
          ▼  LLMClient (interface)
┌────────────────────┐
│ llm/               │  ModelRouter → ModelRegistry → provider type
│ router, registry   │  registry → OllamaProvider / OpenAICompatibleProvider (httpx)
└─────────┬──────────┘
          ▼
   Model server (Ollama, or any OpenAI-compatible local server or cloud API)

Cross-cutting: domain/ (pure models, lifecycle, quality gates, errors),
config/ (YAML settings), observability/ (JSON logging, redaction, clock/ids).
```

LangGraph is the runtime for the workflow, not the business definition of it.
Lifecycle states, allowed transitions, quality gates and approval semantics live
in `domain/` and are enforced by node code, so the orchestration library could
be replaced without redefining the lifecycle.

## Responsibilities by layer

| Layer | Owns | Key files |
|---|---|---|
| `cli` | Argument parsing, rendering status tables, mapping each command to one `FactoryService` method, converting `FactoryError` into an actionable message + exit code 1 | `cli/main.py` |
| `web` | Local browser interface: server-rendered pages, form actions mapped one-to-one to `FactoryService` methods, long actions in a background worker (`JobRunner`) with a polled `status.json`, document rendering (raw HTML disabled), zip download, form token; binds `127.0.0.1` only | `web/app.py`, `web/jobs.py`, `web/templates/*.html` |
| `core` | Project creation/loading, workflow start/resume (incl. `resume_after` for legacy projects, external-change check on `continue`), approval and clarification validation, release guard, pause flag, run lock, recording artifacts and tool logs, build-machine environment facts, `doctor` health checks, `factory evaluate` model evaluation | `core/factory_service.py`, `core/workflow_service.py`, `core/project_recorder.py`, `core/model_evaluation.py` |
| `orchestration` | Graph topology, node behaviour (design, build and quality phase), deterministic routing, typed state; build-phase nodes get their tools from `ToolKit` (project commands via the step's `ProjectCommandTool`; the Coding Agent's files only via its `allowed_tools`); depends on persistence only through the `WorkflowRecorder` port | `orchestration/graph.py`, `orchestration/nodes/*.py` (build: `development_nodes.py`, quality: `quality_nodes.py`), `orchestration/routers/lifecycle_router.py` |
| `agents` | Reasoning steps with a declared contract (inputs, output schema, LLM profile, allowed tools, prompt dir) and deterministic output validation | `agents/base.py`, `agents/*.py` |
| `llm` | Provider-neutral request/response types, error taxonomy, profile→model routing, retries, fallback, structured-output validation and repair; provider adapters | `llm/*.py`, `llm/providers/*.py` |
| `tools` | Deterministic, policy-checked operations: filesystem, git, allowlisted shell, per-step project-command tools (package manager, formatter, lint, build, test, dependency audit), least-privilege `ToolKit`, repository analysis, secret scan, source-tree helpers (file selection, manifest fingerprint) | `tools/*.py` |
| `persistence` | Project registry, checkpoint storage, atomic + redacted artifact writes, Markdown renderers | `persistence/*.py` |
| `domain` | Pydantic models, lifecycle state machine, quality gates, approval/execution records, error hierarchy. Imports nothing else from the package | `domain/*.py` |
| `config` | YAML loading and validation, path resolution, config fingerprint (`FactoryConfig.fingerprint()`, stamped on every execution record) | `config/settings.py` |
| `observability` | JSON-lines logging, secret redaction, UTC timestamps and execution ids | `observability/*.py` |

## Main call path

```text
factory approve my-app
  └─ cli.main.approve
      └─ FactoryService.approve / _decide
          ├─ ApprovalService.build_decision   (validates: waiting for approval? stage guard? reason?)
          ├─ _require_validated_code          (release approval only: HEAD == validated commit, clean tree)
          └─ WorkflowService.resume(project, payload)
              ├─ run lock (fcntl, checkpoints/run.lock)
              ├─ compile graph with WorkflowDependencies
              │    agents + ProjectRecorder + limits + security + analyze_repository
              │    + versions + source_root (projects/<id>/source) + environment
              │    + config_fingerprint
              ├─ open per-project SqliteSaver
              └─ graph.stream(Command(resume=payload), thread_id=project_id)
                   node → recorder.persist_state → LangGraph checkpoint → next node …
                   stops at next interrupt(), END, or when the pause flag is set
```

## Dependency boundaries

Spec §57 boundaries are enforced by `tests/unit/test_dependency_boundaries.py`,
a static import scan (AST) of each layer:

| Layer | Must not import |
|---|---|
| `agents/` | `llm.providers`, `llm.router`, `llm.registry`, `persistence`, `cli`, `core`, `httpx`, `langgraph`, `ollama`, `openai` |
| `orchestration/` | `persistence`, `cli`, `core`, `llm.providers`, `httpx` |
| `domain/` | `persistence`, `cli`, `core`, `llm`, `agents`, `langgraph`, `httpx` |
| `core/` | `llm.providers`, `cli`, `httpx` |
| `web/` | `orchestration`, `persistence`, `llm`, `agents`, `tools`, `cli`, `langgraph` |
| `llm/router.py` | `httpx`, `llm.providers.ollama` |

The same test file also asserts that no model name (`qwen`, `deepseek-`,
`llama3`, `gpt-4`, `gemini-`, `mistral`) appears anywhere in the package:
model names are configuration only.

Actual intra-package imports (as of this version):

```text
cli           → config, core, domain
core          → agents, config, domain, llm, observability, orchestration, persistence, tools
orchestration → agents, config, domain, observability, tools
agents        → domain, llm (interfaces/models only), tools (ToolName only)
llm           → config, domain
tools         → config, domain, observability
persistence   → domain, observability
config        → domain
domain        → (nothing)
observability → (nothing)
```

Only `llm/providers/ollama.py` and `llm/providers/openai_compatible.py` import `httpx`. Only `orchestration/` and
`core/workflow_service.py` / `persistence/checkpoints.py` import LangGraph.

## Key architectural rules as implemented

- **LLM never controls the state machine.** Agents return validated Pydantic
  output; node code decides the next `LifecycleStatus` and validates it with
  `domain.lifecycle.transition()` against `ALLOWED_TRANSITIONS`.
- **AI vs deterministic separation.** Repository analysis, routing, approvals,
  persistence, retries and checkpoints are plain code, and so are the build
  phase's pass/fail decisions (exit codes), commits, security scan, final
  validation and release. Only the seven agents call an LLM.
- **Ground truth from tools.** Build, test and final-validation results come
  from the approved plan commands' real exit codes; agents only explain
  failures and propose fixes. Code is committed only after tests pass.
- **Bounded loops.** Fix attempts (`limits.max_debug_iterations`) and
  review/security rounds (`limits.max_review_rounds`) are capped; exhausting
  them stops for a human.
- **Approval is persistent state.** Each gate pauses via LangGraph
  `interrupt()`; the decision is stored in `approvals` / `decision_log` and in
  `decisions.md`.
- **Quality gates.** Every stage checks `domain.quality_gates` entry criteria
  before it runs; an approval gate refuses to advance if the next stage's
  criteria are unmet.
- **Project isolation.** One directory, one checkpoint database and one
  LangGraph thread per project (see [persistence](persistence_architecture.md)).
- **Provider neutrality.** Agents depend on `LLMClient`; adapters implement
  `ModelProvider`; config chooses the provider type.

## Deviations from the specification's conceptual model

| Spec | Implementation |
|---|---|
| §7 separate `*_REVISION` / `PROCESS_USER_ANSWER` / `CHECK_REQUIREMENT_CLARITY` nodes | Rejection routes straight back to the producing agent node, which reads `revision_feedback`; answers are processed inside `wait_for_user_input`; clarity is decided inside `analyze_requirements` |
| §54 `PAUSED` lifecycle state | Pause is a registry flag (`projects.paused`), orthogonal to lifecycle status |
| §54 state list | Adds `REPOSITORY_ANALYSIS`, `READY_FOR_DEVELOPMENT`, `WAITING_FOR_CHANGE_REQUEST_APPROVAL`, `WAITING_FOR_RELEASE_APPROVAL`, `CANCELLED`; no separate regression-testing state (the full test suite re-runs after every fix and in final validation) |
| §36 change request → impact analysis → revised architecture | Change request → Impact Analysis Agent (inside the change-request update, not a separate node; a failure does not block) → human approval → the target design stage is revised and re-approved |
| §44 security validation agent | Security validation is deterministic code (secret/sensitive-file scan, optional audit command); no LLM |
| §24 "verify server health before execution" | Health is checked by `factory doctor`: the `reasoning` and `coding` profiles each `FAIL` when none of their models (preferred or fallback) is installed and `WARN` when only a fallback is. A workflow run does not pre-check, but an unreachable server or missing model becomes a controlled `FAILED` status with a hint |
| §56 `domain/state.py`, `persistence/repositories.py`, `tools/build.py`/`test.py`/`lint.py` | State is `orchestration/state.py`; registry is `persistence/database.py`; build/test/lint/format/package-manager/audit tools live in `tools/project_commands.py` and are used by the build phase through `tool_for_step` |

See also [setup guide](../setup/setup_guide.md) and
[configuration](../setup/configuration.md).
