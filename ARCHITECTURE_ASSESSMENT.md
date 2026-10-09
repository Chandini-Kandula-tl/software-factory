# Architecture assessment

Produced per spec §61 step 2 before and during implementation. It records
where the repository started, the architecture chosen, and what remains.

## 1. Starting state (inspection, 2026-09-29)

| Item | Finding |
|---|---|
| Repository | Empty — a previous attempt was deleted; implementation started from scratch |
| Python | System 3.9.6 (too old for LangGraph); uv-managed 3.11.16 available → used |
| Package tooling | uv 0.12 |
| Current package versions | langgraph 1.2, langgraph-checkpoint-sqlite 3.1, pydantic 2.13, typer 0.27, httpx 0.28 |
| Local model server | **Ollama not installed**, nothing listening on `localhost:11434` |
| Hardware | Apple M1 Pro, 10 cores, 32 GB RAM → 7B–14B models at Q4 run comfortably |
| Git | 2.50 available; new repository initialised, no commits |

**Main constraint:** without a running model server, live LLM runs cannot be
demonstrated on this machine yet. Everything up to the provider boundary is
verified with a scripted provider and a mocked Ollama HTTP server.

## 2. Architecture

```text
CLI (typer)  ──►  Factory Core (FactoryService, ProjectService, WorkflowService,
                                ApprovalService, ClarificationService, doctor)
                        │
                        ▼
              LangGraph workflow (graph, lifecycle router, nodes)
                 │               │                    │
                 ▼               ▼                    ▼
        Agents (LLMAgent)   Deterministic tools   WorkflowRecorder port
                 │           (repo analysis, fs,   (implemented by Core →
                 ▼            shell, git, build)    persistence)
        LLMClient interface
                 │
                 ▼
        ModelRouter + ModelRegistry  ──►  provider type registry  ──►  OllamaProvider
```

Layer rules (enforced by `tests/unit/test_dependency_boundaries.py`):

- Agents import only the LLM *interface*, tool types and domain models — never
  providers, persistence, the CLI, HTTP or LangGraph.
- The workflow imports agents and domain state; persistence reaches it through
  the `WorkflowRecorder` port.
- The domain layer is pure.
- Factory Core never imports a provider module.
- No model name appears anywhere in the source.

Detailed documents: [docs/architecture/](docs/architecture/system_architecture.md).

## 3. Gaps against the full specification

| Area | Status |
|---|---|
| Design phase: requirements → clarification → architecture → technology → plan, 4 approval gates | **Done** — verified live with Ollama |
| Persistence, checkpoints, resume after restart / kill, pause, execution history | **Done** |
| LLM abstraction, profiles, capabilities, retries, fallback, structured output | **Done** |
| Ollama adapter + `factory doctor` (health, models, profiles, RAM check, `--probe`) | **Done** — verified live |
| Tool layer: filesystem, shell, git, secret scan, source context, repository analysis | **Done** — used by the build phase |
| Coding, Testing, Code Review agents; security validation (deterministic); build/test/debug loop; final validation; release gate | **Done** — automated tests with real build/test runs; first live run (todo app, 2026-09-30) reached bug fixing and its findings are fixed (ADR-033…040); a complete live run to release is pending |
| Change-request flow | **Done** — same caveat |
| Existing-project mode | **Done** — repository analysis + development on a `factory/<id>` branch; impact analysis is by the agents, not a separate step |
| Other engines & cloud APIs | **Done** — `openai_compatible` adapter, verified live against Ollama's `/v1` |
| Model evaluation (§68.17) | **Done** — `factory evaluate`, 5/5 live with `qwen2.5:14b`; `code_generation` also passes with `qwen2.5-coder:14b` (shipped coding model) |
| Resume validation (§33), exact release (§68.15), reproducibility (§68.16) | **Done** |
| Sandboxed execution of generated code | **Not started** — code runs on the host inside the project folder with an allowlist |
| Custom workflow definitions, auto model download | **Future** (spec allows) |

## 4. Risks

| Risk | Mitigation |
|---|---|
| Local models produce invalid or shallow structured output | Schema-constrained generation, bounded repair loop, capability-compatible fallback, human approval at every gate, `doctor --probe` before use |
| Slow local inference (minutes per stage) | Generous `timeout_seconds`, checkpoint after every node, pause/continue |
| Model choice varies per machine | Model names only in config; `docs/models/model_selection.md`; RAM check in `doctor` |
| LangGraph API changes | Only `StateGraph`, `interrupt`, `Command`, `SqliteSaver` are used, all behind `orchestration/` and `persistence/checkpoints.py` |
| Small local models get stuck on logic bugs (seen live: JSON keys loaded as strings, id reuse after delete) | Coder model for the `coding` profile, pitfalls checklist in the prompt, several really-checked fix candidates per round, failed-attempt memory, stuck detection (`No progress: …`) and `factory retry --hint` for a human diagnosis |
| Generated project silently relies on the factory's own tools or repository | Project commands run without the factory venv (`security.isolate_project_environment`); greenfield `source/` is always its own git repository |
| Two 14B models exceed memory | `OLLAMA_MAX_LOADED_MODELS=1` (one model loaded, ~15 GB at 32k context) |
| Shell/git tools used unsafely once agents get them | Allowlist, forbidden executables, confined cwd, timeouts, env scrubbing, destructive git requires approval |

## 5. Recommended sequence from here

1. Finish a build-phase run end to end with the real models
   (`qwen2.5:14b` + `qwen2.5-coder:14b`) on a small Python project and tune
   the coding/testing/review prompts on real output.
2. Sandboxed execution for generated code before any production use.
