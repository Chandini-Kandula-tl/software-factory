# Implementation plan

Per spec §61 step 3. Scope labels: **MVP** (first vertical slice), **Next**
(build phase, spec §70 "should have"), **Future**, **Out of scope**.

## Phases and status

| Phase (spec §59) | Scope | Status |
|---|---|---|
| 0 — Repository & architecture | MVP | Done |
| 1 — Local LLM foundation (interface, registry, router, Ollama adapter, health, structured output) | MVP | Done |
| 2 — Agent foundation (contract, Requirement, Architecture, Technology Selection) | MVP | Done (+ Implementation Planning, ADR-009) |
| 3 — LangGraph workflow (typed state, nodes, routing, clarification, approval, pause/resume) | MVP | Done |
| 4 — Persistence (projects, artifacts, state, history, checkpoints) | MVP | Done |
| 5 — CLI | MVP | Done |
| 6 — MVP integration (create → … → stop → restart → continue) | MVP | Done — verified live with Ollama (qwen2.5:7b and 14b) |
| 7 — Development tools | Next | Done — used by the build phase |
| 8 — Coding workflow (plan → code → build → test → debug → review) | Next | Done — first live run with a real model (todo app, 2026-09-30) reached bug fixing; its findings are addressed (below); a complete live run to release is pending |
| 9 — Quality (security, final validation, change requests, release) | Next | Done — automated tests only; live run pending |
| 10 — Provider expansion | Future | Done — `openai_compatible` adapter (LM Studio, llama.cpp, vLLM, DeepSeek, OpenAI, …), verified live against Ollama's `/v1` |

## Findings of the first live build run (todo app, 2026-09-30)

The first full build with a real local model (`qwen2.5:14b` for every agent)
exposed problems the scripted tests could not. All are fixed in code, recorded
as ADRs and documented:

| Finding | Change | ADR |
|---|---|---|
| The factory's own venv made `python`/`pytest` look installed to the generated project | Project commands run without the factory venv (`security.isolate_project_environment`, default `true`); the same lookup for environment facts and the plan check | ADR-033 |
| The workspace lies inside the factory's git checkout, so git found the wrong repository | Greenfield `source/` is always its own repository (`git init` if `source/.git` is missing, `--show-toplevel` check) | ADR-034 |
| Tests writing a data file blocked the release guard | Uncommitted changes are checked before the final run; files the run writes are `validation.test_side_effects` (warning) and ignored by the release guard and resume check | ADR-035 |
| Whole-file rewrites for one-line fixes; duplicate paths rejected; fixable output errors failed the stage | Coding Agent `edits` (exact, unique `find`); duplicate paths keep the last copy; one correction attempt with the exact reason (`validation_repairs`) | ADR-036 |
| The model could not find two logic bugs (JSON keys come back as strings; id reuse after delete) and blamed correct tests | `factory retry --hint`; prompts: coding system 1.4.0 / task 1.2.0, testing 1.1.0, planning system 1.2.0 (small tasks, skeleton first) | ADR-037 |
| The same wrong fix was repeated until the budget ran out | Stuck detection (3 identical failures → `No progress: …`), failed-attempt memory | ADR-038 |
| Local answers vary; one bad answer cost a whole round | Up to `limits.fix_candidates` (3) fixes per round, each really checked, first passing one kept | ADR-039 |
| One general model for coding | `qwen2.5-coder:14b` for the `coding` profile (fallback `qwen2.5:14b`, temperature 0.2), `OLLAMA_MAX_LOADED_MODELS=1` (~15 GB per 14B model at 32k) | ADR-040 |

Known limitation: small local models still miss some logic bugs and may need
a human hint ([model selection](docs/models/model_selection.md#the-coding-role)).

## What the build phase does

```text
plan approved
  → prepare_development   check programs installed (outside the factory venv) + allowed,
                          create source/ as its own git repo
                          (or a factory/<id> branch of an existing clean repo)
  → for each task (dependency order):
       implement_task     Coding Agent proposes files / edits → written via FilesystemTool
       build              setup (if manifests changed) → format → lint → build
       test               the plan's test command — exit code decides
         pass → git commit "T-n: title" → next task
         fail → debug     Testing Agent explains → Coding Agent proposes up to
                          limits.fix_candidates fixes, each really checked; first
                          passing one kept → build
                          (≤ limits.max_debug_iterations rounds, then FAILED; the same
                           failure 3× → FAILED "No progress"; retry [--hint] restores
                           budget; "environment" failures stop immediately for a human)
  → code_review           Code Review Agent; critical/high findings → fix → review again
                          (≤ limits.max_review_rounds, then continue with a warning)
  → security_validation   secret + sensitive-file scan, dependency audit → must be clean
  → final_validation      clean git before the run, full re-run, all tasks done,
                          traceability; files written by the run → warning
  → release_approval      human: approve, or reject with what to fix (fix cycle → review again)
  → release               git tag release-N, release-notes.md → COMPLETED

Change request (from implement_task or debug) → human: approve → redo that design
stage and later ones → approve plan again → development continues; reject → continue
within the approved decisions.
```

## Files

| Area | Files |
|---|---|
| Domain | `domain/` — `lifecycle.py`, `quality_gates.py`, `project.py`, `requirements.py`, `architecture.py`, `technology.py`, `planning.py` (incl. `ProjectCommands`), `development.py` (code changes, change requests, test analysis, review, security, verification, validation, release), `approvals.py`, `execution.py`, `repository.py`, `errors.py` |
| LLM | `llm/` — `interfaces.py`, `models.py`, `errors.py`, `registry.py`, `router.py`, `providers/base.py`, `providers/ollama.py`, `providers/openai_compatible.py` |
| Agents | `agents/` — `base.py`, `prompts.py`, `requirement.py`, `architecture.py`, `technology_selection.py`, `implementation_planning.py`, `coding.py`, `testing.py`, `code_review.py`, `impact_analysis.py`, `catalog.py`; prompts in `prompts/<agent>/` |
| Workflow | `orchestration/` — `graph.py`, `state.py`, `ports.py`, `routers/lifecycle_router.py`, `nodes/{agent,human,deterministic,development,quality}_nodes.py`, `nodes/common.py` |
| Core | `core/` — `factory_service.py`, `project_service.py`, `workflow_service.py`, `approval_service.py`, `clarification_service.py`, `project_recorder.py`, `doctor.py`, `model_evaluation.py` |
| Web UI | `web/` — `app.py` (routes), `jobs.py` (background actions), `templates/`, `static/style.css` |
| Persistence | `persistence/` — `database.py`, `checkpoints.py`, `artifacts.py`, `markdown.py` |
| Tools | `tools/` — `base.py`, `filesystem.py`, `shell.py`, `git.py`, `project_commands.py`, `repository_analysis.py`, `secret_scan.py`, `source_workspace.py`, `toolkit.py` |
| CLI / config / observability | `cli/main.py`, `config/settings.py`, `config/factory.yaml`, `observability/{logging,redaction,clock}.py` |

## Interfaces

- `LLMClient` — `generate`, `generate_structured(request, schema)`, `supports(capability, profile)`.
- `ModelProvider` — `generate(request, model, options)`, `health_check()`, `get_model_metadata(model)`.
- `LLMAgent.run(AgentContext) -> AgentResult[Output]` with a declarative `AgentSpec`
  (inputs, output schema, profile, tools, prompt dir) and deterministic `validate_output`.
- `WorkflowRecorder` — `persist_state`, `write_stage_artifact`, `record_execution`, `write_tool_output`.

## Testing strategy

| Suite | What it proves |
|---|---|
| `tests/workflow/test_design_workflow.py` | Spec §51 acceptance scenario incl. restarts; clarification rounds, limit and repeated questions; rejection/revision; failure → retry / cancel; kill mid-node → resume; pause; isolation; existing-project analysis; secrets never persisted |
| `tests/workflow/test_build_workflow.py` | Real files, real git, real `python -m unittest` runs with scripted agent answers: full run to release; failing test → analysis → fix → commit only when green; fix budget exhausted → retry; fix candidates, stuck detection, retry with a hint; environment failures stop; review findings fixed and re-reviewed; review round limit; hard-coded secret blocked until removed; change request approved/rejected; release rejected → fix cycle; missing program; existing repo on a branch; dirty repo refused; writes outside the repo refused |
| `tests/integration/` | Same workflow on the Ollama adapter (mocked HTTP), another provider type and an OpenAI-compatible API with config-only changes (spec §68.2); CLI first-run flow; `doctor`; web UI: full journey through the pages to release, reject/retry-with-hint, form token, no HTML execution, document whitelist, one action per project |
| `tests/unit/` | OpenAI-compatible adapter; model evaluation; Ollama errors; router retry/fallback/capabilities/repair; state machine incl. build phase; quality gates; config; redaction; secret scan; source context; prompts; agent contracts; tools; dependency boundaries |

Run: `uv run pytest`, `uv run ruff check src tests`, `uv run mypy`.

## Next

1. ✅ **Done 2026-09-30:** the todo app ran live from idea to `release-1`
   (Python CLI, 4 tasks, 16 tests). It needed 4 human hints for logic bugs;
   fix candidates solved one task on their own. The generated app was checked
   by hand and works.
2. ✅ **Done:** local web UI (`factory ui`) — see the
   [Web UI guide](docs/setup/web_ui_guide.md).
3. Run a second live project through the web UI with the two-model setup and
   measure how many hints are needed; consider `qwen2.5-coder:32b` or an
   OpenAI-compatible cloud model for coding if hints stay frequent.
4. Run an existing-repository project live.
5. Sandboxed execution (e.g. containers) for generated code — today it runs on
   the host inside the project folder, limited by the command allowlist
   (spec §44 lists this for future production use).

## Future

Custom workflow definitions (spec §68.8), automatic model download /
onboarding (§68.1, optional), parallel agents, advanced routing, enterprise
policy engine.

## Out of scope for now

Autonomous deployment, distributed workers, RAG, browser automation, cloud
infrastructure, model benchmarking (spec §50, §58).

## Acceptance criteria (MVP, spec §60)

- [x] Isolated projects · [x] state persisted · [x] resume after restart
- [x] Requirement Agent · [x] clarification workflow · [x] Architecture Agent
- [x] Technology Selection Agent · [x] human approval · [x] LangGraph controls the workflow
- [x] Local model server works — verified 2026-09-29 with Ollama (`qwen2.5:7b`, `qwen2.5:14b`)
- [x] Provider abstracted · [x] model changed through configuration
- [x] Execution history · [x] checkpoints · [x] tests cover core workflows
- [x] Secrets not persisted · [x] project context isolated · [x] documentation

## Final product definition (spec §71) — status

| # | Capability | Status |
|---|---|---|
| 1–3 | Install, configure a local model, health check | Done |
| 4–8 | Create project, requirements, clarification, approve architecture & technology | Done, verified live |
| 9 | Execute the approved development workflow | Implemented; first live run (2026-09-30) reached bug fixing, findings fixed; complete live run pending |
| 10 | Pause and resume at any time | Done |
| 11 | Existing repository | Implemented (analysis + branch-based development); live run pending |
| 12–13 | Review what agents did; inspect real build/test/review results | Done (history, tool logs, reports) |
| 14 | Validated final artifact | Implemented (final validation + release gate); live run pending |
| 15 | Switch local model → LLM API by configuration | Done — `openai_compatible` provider (config only) |
