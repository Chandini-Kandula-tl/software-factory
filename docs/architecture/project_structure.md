# Project Structure

The repository layout as it exists today, and what each folder is for
(spec §73.10). Update this file whenever the structure changes
significantly.

## Top level

```text
software-factory/
├── pyproject.toml        package metadata, `factory` console script, ruff/mypy/pytest config
├── uv.lock               locked dependency versions (uv)
├── .python-version       Python version for uv/pyenv
├── .env.example          template for environment variables (real .env is gitignored)
├── .gitignore            ignores venvs, caches, .env*, keys, factory-workspace/, *.sqlite, *.db
├── README.md
├── ARCHITECTURE_ASSESSMENT.md  starting state, architecture, gaps, risks (spec §61 step 2)
├── IMPLEMENTATION_PLAN.md      phases, status, MVP vs next vs future (spec §61 step 3)
├── config/               factory configuration (YAML)
├── prompts/              versioned agent prompts (Markdown)
├── src/software_factory/ the factory itself
├── tests/                unit, integration and workflow tests
├── docs/                 Software Factory documentation (this folder)
├── examples/             sample inputs (e.g. employee-management/requirements.md)
└── factory-workspace/    RUNTIME DATA — created on first run, gitignored
```

## Source code: `src/software_factory/`

```text
src/software_factory/
├── __init__.py                   __version__ ("0.2.0"), WORKFLOW_VERSION ("full-lifecycle-1.0")
├── cli/
│   ├── __init__.py
│   └── main.py                   Typer app: create, requirements, list, start, continue, status,
│                                 pause, approve, reject, questions, answer, retry, cancel,
│                                 history, doctor, evaluate, ui, version
├── web/                          local web UI (`factory ui`) — calls Factory Core only
│   ├── __init__.py
│   ├── app.py                    FastAPI routes: pages, form actions, status.json, download;
│   │                             Markdown rendering with raw HTML disabled; form token
│   ├── jobs.py                   JobRunner: long actions in a worker thread, one per project
│   ├── templates/                Jinja2 pages: base, index, new, project, document, history,
│   │                             doctor, _status_badge
│   └── static/style.css          light/dark stylesheet (no JavaScript framework)
├── core/                         Factory Core — the only layer the CLI calls
│   ├── __init__.py
│   ├── factory_service.py        facade: one method per command; resume check for external
│   │                             repository changes, release guard (validated commit only)
│   ├── project_service.py        create/load projects, id validation, project dirs
│   ├── workflow_service.py       compile/run/resume the graph, resume_after, pause check,
│   │                             run lock, snapshots, build-machine environment facts
│   ├── approval_service.py       validate approve/reject input (any gate), build resume payload
│   ├── clarification_service.py  save answers, build clarification resume payload
│   ├── project_recorder.py       WorkflowRecorder implementation: mirrors state to files,
│   │                             stage/latest-only artifacts, tool logs, pending actions
│   ├── doctor.py                 `factory doctor` environment/model checks
│   └── model_evaluation.py       `factory evaluate`: real agents on small fixed cases, checked
│                                 by code; reports saved in <workspace>/evaluations/
├── orchestration/                LangGraph workflow (runtime of the lifecycle)
│   ├── __init__.py
│   ├── graph.py                  23 nodes, conditional edges, compile (docstring = ASCII flow)
│   ├── state.py                  FactoryState TypedDict
│   ├── ports.py                  WorkflowRecorder protocol, WorkflowDependencies
│   ├── nodes/
│   │   ├── __init__.py
│   │   ├── agent_nodes.py        analyze_requirements, design_architecture,
│   │   │                         select_technology, create_implementation_plan
│   │   ├── deterministic_nodes.py load_project, analyze_repository (+ impact analysis,
│   │   │                         existing projects)
│   │   ├── development_nodes.py  build phase: prepare_development, implement_task, build,
│   │   │                         test, debug; PHASE_RETURN; prepare_retry; command_problems
│   │   │                         (plan environment check); shared helpers (_run_commands, …)
│   │   ├── human_nodes.py        wait_for_user_input, approval gates (4 design + release),
│   │   │                         change_request_approval, handle_failure
│   │   ├── quality_nodes.py      code_review, security_validation, final_validation, release;
│   │   │                         _traceability
│   │   └── common.py             execution records, failure updates, invoke_agent, timer
│   └── routers/
│       ├── __init__.py
│       └── lifecycle_router.py   status → next node
├── agents/                       reasoning components (LLM via LLMClient only)
│   ├── __init__.py
│   ├── base.py                   AgentSpec, AgentContext, AgentResult, LLMAgent
│   ├── catalog.py                IMPLEMENTED_AGENTS, FactoryAgents, build_agents
│   ├── prompts.py                PromptLibrary: loads prompts/<agent>/*.md with versions
│   ├── requirement.py            Requirement Agent          ("reasoning" profile)
│   ├── architecture.py           Architecture Agent         ("reasoning")
│   ├── technology_selection.py   Technology Selection Agent ("reasoning")
│   ├── implementation_planning.py Implementation Planning Agent ("reasoning")
│   ├── coding.py                 Coding Agent: CodeChanges / change request ("coding")
│   ├── testing.py                Testing Agent: TestFailureAnalysis ("coding")
│   ├── code_review.py            Code Review Agent: ReviewReport ("coding")
│   └── impact_analysis.py        Impact Analysis Agent: ImpactAnalysis ("reasoning")
├── llm/                          LLM abstraction
│   ├── __init__.py
│   ├── interfaces.py             LLMClient (agents), ModelProvider (adapters)
│   ├── models.py                 LLMRequest/LLMResponse, Capability, ProviderCall, metadata
│   ├── errors.py                 LLMErrorKind, RETRYABLE_KINDS, FALLBACK_KINDS, LLMError
│   ├── registry.py               ModelRegistry: providers, models, profiles, candidates
│   ├── router.py                 ModelRouter: selection, retry, fallback, structured output
│   └── providers/
│       ├── __init__.py
│       ├── base.py               provider type registry, read_api_key
│       ├── ollama.py             OllamaProvider (provider type "ollama")
│       └── openai_compatible.py  OpenAICompatibleProvider (provider type "openai_compatible")
├── tools/                        deterministic, policy-checked tools
│   ├── __init__.py
│   ├── base.py                   ToolName, ToolResult, ToolPermissionError
│   ├── filesystem.py             FilesystemTool (root-confined)
│   ├── git.py                    GitTool (safe vs dangerous operations)
│   ├── shell.py                  ShellTool (allowlist, timeout, env scrub, redaction)
│   ├── project_commands.py       ProjectCommandTool and BuildTool, TestTool, LintTool,
│   │                             FormatterTool, PackageManagerTool, DependencyAuditTool;
│   │                             STEP_TOOLS, tool_for_step (plan step → tool)
│   ├── repository_analysis.py    analyze_repository (existing-project facts)
│   ├── secret_scan.py            secret / sensitive-file detection for generated code
│   ├── source_workspace.py       source file listing, budgeted file selection, manifest
│   │                             fingerprint, default .gitignore
│   └── toolkit.py                ToolKit: shell(), tools_for / require (agent allowed_tools)
├── persistence/
│   ├── __init__.py
│   ├── database.py               ProjectRegistry (factory.db)
│   ├── checkpoints.py            per-project SqliteSaver, thread config
│   ├── artifacts.py              ArtifactStore: atomic, redacted writes; history; answers
│   └── markdown.py               Markdown renderers for project artifacts (design + build)
├── domain/                       pure models and rules (imports nothing else in the package)
│   ├── __init__.py
│   ├── project.py                Project, ProjectMode, id slug/validation
│   ├── lifecycle.py              LifecycleStatus, ALLOWED_TRANSITIONS, transition()
│   ├── quality_gates.py          stage entry criteria
│   ├── approvals.py              ApprovalStage, ApprovalStatus, ApprovalDecision
│   ├── requirements.py           RequirementAnalysis, clarification question/answer models
│   ├── architecture.py           ArchitectureProposal
│   ├── technology.py             TechnologySelection
│   ├── planning.py               ImplementationPlan, ImplementationTask, ProjectCommands
│   ├── development.py            build-phase schemas: CodeChanges, ChangeRequest,
│   │                             TestFailureAnalysis, ReviewReport, CommandRun,
│   │                             VerificationResult, SecurityReport, ValidationReport,
│   │                             TraceRow, ReleaseRecord, ImpactAnalysis
│   ├── repository.py             RepositoryAnalysis, GitState
│   ├── execution.py              ExecutionRecord, LLMCallRecord
│   └── errors.py                 FactoryError hierarchy
├── config/
│   ├── __init__.py
│   └── settings.py               FactoryConfig (YAML), load_config, fingerprint
└── observability/
    ├── __init__.py
    ├── logging.py                JSON-lines logging with redaction
    ├── redaction.py              secret redaction
    └── clock.py                  utc_now, new_execution_id
```

Differences from the spec §56 suggestion: agent files are named after the
agent (`technology_selection.py`, `implementation_planning.py`); state lives
in `orchestration/state.py`; build/test/lint share
`tools/project_commands.py`; the registry is `persistence/database.py`; prompts
live at the repository root, not inside the package. There is no security
validation agent: security validation is deterministic code in
`quality_nodes.py` using `tools/secret_scan.py`.

Dependency rules between these folders:
[system architecture](system_architecture.md#dependency-boundaries).

## Prompts: `prompts/`

```text
prompts/
├── requirement/              system.md, task.md
├── architecture/             system.md, task.md
├── technology_selection/     system.md, task.md
├── implementation_planning/  system.md, task.md
├── coding/                   system.md, task.md
├── testing/                  system.md, task.md
├── code_review/              system.md, task.md
└── impact_analysis/          system.md, task.md
```

Each file starts with `---\nversion: x.y.z\n---`. The folder name equals the
agent's `AgentSpec.prompt_dir`. Location is `factory.prompts_dir` in config.

## Configuration: `config/`

```text
config/
└── factory.yaml    providers, models, profiles, limits, security, logging, workspace path
```

Resolved from `--config`, then `$FACTORY_CONFIG`, then
`./config/factory.yaml`. Relative paths inside it resolve against `config/`.
See [configuration](../setup/configuration.md).

## Tests: `tests/`

```text
tests/
├── __init__.py
├── conftest.py                    shared fixtures (config in tmp dir, scripted provider)
├── support/
│   ├── __init__.py
│   ├── fixtures.py                canned agent outputs
│   └── scripted_provider.py       deterministic ModelProvider, provider type "scripted"
├── unit/
│   ├── __init__.py
│   ├── test_agents.py             agent contract, minimal context, output validation
│   ├── test_build_tools.py        secret scan, source-workspace helpers, build-phase transitions
│   ├── test_dependency_boundaries.py  layer import rules, no hard-coded model names
│   ├── test_domain_and_config.py  lifecycle, quality gates, config, redaction, prompts
│   ├── test_model_evaluation.py   `factory evaluate` cases with scripted good/weak answers
│   ├── test_model_router.py       profiles, capability checks, retry, fallback, repair
│   ├── test_ollama_provider.py    Ollama adapter against mocked HTTP
│   ├── test_openai_compatible_provider.py  OpenAI-compatible adapter against mocked HTTP
│   └── test_tools.py              filesystem, shell, git, project-command tools, ToolKit,
│                                  repository analysis
├── integration/
│   ├── __init__.py
│   ├── test_cli.py                end-to-end CLI design flow, error messages, doctor
│   └── test_provider_switching.py same workflow on Ollama adapter and another provider type
└── workflow/
    ├── __init__.py
    ├── test_design_workflow.py    full design phase: restart, clarification rounds, rejection,
    │                              retry, cancel, kill mid-run, pause, isolation, existing project,
    │                              secret redaction
    └── test_build_workflow.py     build phase with real files, git and `python -m unittest`:
                                   full run to release, fix loop, fix budget + retry, environment
                                   failures, review loop/limit, secret blocking + retry with manual
                                   fix, change request approve/reject, release rejection, missing
                                   programs, cwd outside repo, existing-repo branch, dirty repo,
                                   path escape, external changes + release guard, missing
                                   planned test files
```

No test needs a running model server. Build-workflow tests need `git` and
`python` on the machine.

## Documentation: `docs/`

Software Factory documentation (how the factory works), separate from the
generated project documentation written into each project directory
(spec §73.12).

```text
docs/
├── README.md                     index
├── architecture/                 system, LLM, orchestration, persistence, security, this file
├── workflows/                    execution flow (source of truth), greenfield, existing project,
│                                 change request
├── agents/                       one file per agent (implemented and planned)
├── tools/                        one file per tool
├── models/                       local model setup, model configuration, model selection
├── setup/                        setup guide, configuration, troubleshooting
└── decisions/                    architecture decisions
```

Start at [docs/README.md](../README.md).

## Runtime workspace: `factory-workspace/` (gitignored)

Created on first use at the path in `factory.workspace`. Never commit it.

```text
factory-workspace/
├── factory.db            project registry
├── logs/factory.log      JSON-lines log
├── evaluations/          `factory evaluate` reports (evaluation-<timestamp>.json)
└── projects/<id>/        one directory per project: Markdown + JSON artifacts,
                          state.json, execution-history.json, artifacts/ (incl. tool-runs/),
                          checkpoints/, source/ (generated repository, greenfield)
```

Full per-project layout: [persistence](persistence_architecture.md#workspace-layout).
