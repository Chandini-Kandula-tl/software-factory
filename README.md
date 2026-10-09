# Software Factory

A generic, local-first, LLM-provider-agnostic **AI Software Factory**. It takes
a software idea or requirements (or a change to an existing code base) and
drives it through the whole lifecycle — requirements, clarification, impact
analysis, architecture, technology, planning, coding,
build, test, bug fixing, code review, security validation, final validation
and release — with **human approval gates** at every important decision and
**persistent, resumable state** throughout.

- **LangGraph** orchestrates the workflow; the factory owns its own domain
  model, lifecycle state machine and interfaces.
- **Agents never call a model provider directly.** They use a factory-owned
  `LLMClient`; a model router picks a configured model by profile and
  capability. Providers: a local **Ollama** server, and any
  **OpenAI-compatible** engine or API (LM Studio, llama.cpp, vLLM, DeepSeek,
  OpenAI, …). Switching models or providers is a configuration change.
- **Deterministic code decides.** Agents propose; the workflow decides
  transitions, approvals are persisted state, and tool results (not agent
  claims) are ground truth.
- **Only real results count.** Code is committed only after the project's own
  test command passes; "tests passed" always comes from an exit code.
- **Every loop is bounded.** Fix attempts and review rounds have limits, and
  the same failure three times in a row stops the run; then the factory asks
  a human, who can steer the next attempt with `factory retry --hint "…"`.
- **Generated projects stand on their own.** Project commands run without the
  factory's own Python environment, and a new project's `source/` is always
  its own git repository.
- **Everything is readable.** Each project keeps Markdown artifacts for
  humans and JSON for machines, plus a full execution history.

**Version 0.2.0.** The full lifecycle is implemented and covered by automated
tests (scripted model answers, real files, git and test runs). With real local
models the design phase has run end to end and `factory evaluate` passes. The
first live full build (a todo app, 2026-09-30) reached the build phase and
showed that `qwen2.5:14b` alone needs human hints for some logic bugs; that
led to the two-model setup (`qwen2.5:14b` for reasoning, `qwen2.5-coder:14b`
for coding), fix candidates, stuck detection and `retry --hint`. See
[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md).

## Quick start

Prerequisites: [uv](https://docs.astral.sh/uv/), git, and
[Ollama](https://ollama.com) for real runs. Full instructions:
[docs/setup/setup_guide.md](docs/setup/setup_guide.md).

```bash
uv sync
```

Start Ollama so only one model is in memory at a time (a loaded 14B model
takes ~15 GB at 32k context):

```bash
OLLAMA_MAX_LOADED_MODELS=1 ollama serve
```

Then, in another terminal, pull the two configured models — `qwen2.5:14b`
(reasoning agents, and fallback for coding) and `qwen2.5-coder:14b` (Coding,
Testing and Code Review agents):

```bash
ollama pull qwen2.5:14b
```

```bash
ollama pull qwen2.5-coder:14b
```

```bash
uv run factory doctor --probe
```

```bash
uv run factory evaluate
```

```bash
uv run factory create "Employee Management" --requirements-file examples/employee-management/requirements.md
```

```bash
uv run factory start employee-management
```

**Prefer a browser?** Instead of the commands above, run:

```bash
uv run factory ui
```

It opens a local page (only on your computer) where you create projects,
answer questions, read and approve each document, give hints, and download the
finished app. See the [Web UI guide](docs/setup/web_ui_guide.md).

In the terminal, the factory then waits for you. Typical loop:

| When the status is… | Do |
|---|---|
| `WAITING_FOR_REQUIREMENT_CLARIFICATION` | `factory questions <id>` → `factory answer <id> Q1 "…"` → `factory continue <id>` |
| `WAITING_FOR_*_APPROVAL` | Read the named `.md` artifact → `factory approve <id>` or `factory reject <id> --reason "…"` |
| (after plan approval) | The factory codes each task, runs the plan's build/test commands, fixes failures, reviews, security-scans and validates on its own |
| `WAITING_FOR_CHANGE_REQUEST_APPROVAL` | The Coding Agent says an approved decision can't work — read `change-request.md` → approve (redo that design step) or reject |
| `WAITING_FOR_RELEASE_APPROVAL` | Read `validation.md`, `review.md`, `security.md`, try the code in `source/` → approve (release) or reject with what to fix |
| `FAILED` | Read `errors.md` → fix (e.g. start Ollama, install Node.js) → `factory retry <id>` |
| `FAILED` with `No progress: …` (agents stuck on a bug) | Read `test-results.md` / `test-analysis.md` → `factory retry <id> --hint "what the real problem is"` |
| Anything, after closing the terminal or a crash | `factory continue <id>` |
| `COMPLETED` | Released: git tag `release-N` and `release-notes.md`. To change the app later: `factory update <id> --text "what to change"` (or **Update this app** in the web UI) |

Commands: `create`, `requirements`, `start`, `status`, `questions`, `answer`,
`continue`, `approve`, `reject`, `retry [--hint "…"]`, `cancel`, `pause`,
`history`, `list`, `doctor [--probe]`, `evaluate [--case NAME]`, `update <project> --text "…"`, `ui`, `delete <project>`, `version`.
Run `uv run factory --help`.

Project artifacts live in `factory-workspace/projects/<project-id>/`:

- design: `requirements.md`, `architecture.md`, `technology-stack.md`,
  `implementation-plan.md` (each with a `.json` twin and per-revision copies in
  `artifacts/`);
- build: `source/` (the generated code, its own git repository with one commit
  per task), `test-results.md`, `test-analysis.md`, `review.md`, `security.md`,
  `validation.md` (with requirement → task → commit → test traceability),
  `release-notes.md`, full command logs in `artifacts/tool-runs/`;
- always: `decisions.md`, `progress.md`, `errors.md`, `state.json`,
  `execution-history.json`.

Existing code bases: `factory create <name> --repository <path>` works on a
`factory/<project-id>` branch of that (clean) repository.

## Development

```bash
uv run pytest
```

```bash
uv run ruff check src tests
```

```bash
uv run mypy
```

Tests need no model server: they use a scripted provider and a mocked Ollama
HTTP API.

## Documentation

Start at [docs/README.md](docs/README.md). Key documents:

- [Setup guide](docs/setup/setup_guide.md) · [Configuration](docs/setup/configuration.md) · [Troubleshooting](docs/setup/troubleshooting.md)
- [Execution flow](docs/workflows/software_factory_execution_flow.md)
- [System architecture](docs/architecture/system_architecture.md) · [Project structure](docs/architecture/project_structure.md)
- [Architecture decisions](docs/decisions/architecture_decisions.md)
- [Architecture assessment](ARCHITECTURE_ASSESSMENT.md) · [Implementation plan](IMPLEMENTATION_PLAN.md)

Documentation is part of every change: when behavior, workflow, configuration,
tools or structure change, update the matching Markdown in the same change.
