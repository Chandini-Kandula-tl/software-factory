# Model selection

No single model suits every machine. The factory does not choose a model for
you: models are configuration (`config/factory.yaml`), and `factory doctor`
checks whether the ones you configured are installed, fit your RAM and can
produce the structured output the agents need.

This page explains what to consider and gives starting points by RAM. Treat
them as starting points — **verify a model with `factory evaluate` before
relying on it** (see [below](#verify-with-factory-evaluate)), after
`factory doctor --probe` has shown it answers at all.

Models can be local (Ollama, or any OpenAI-compatible local server such as
LM Studio) or hosted (DeepSeek, OpenAI, Groq, OpenRouter, … through the
`openai_compatible` provider type). Switching is configuration only — see
[model configuration](model_configuration.md#openai-compatible-servers-and-cloud-apis).
The RAM tables below apply to local models; for a hosted model, memory is not
your concern but cost, rate limits and sending your requirements and code to
a third party are.

> Automatic hardware detection and model recommendation is **Planned — not
> implemented yet**. Doctor currently reports total RAM against model size
> only; it does not detect GPU/VRAM.

## What the factory needs from a model

The workflow has two model roles:

- **Design phase** — Requirement, Architecture, Technology Selection,
  Implementation Planning and Impact Analysis agents, on the `reasoning` profile.
- **Build phase** — Coding, Testing and Code Review agents, on the `coding`
  profile (see [the coding role](#the-coding-role)).

Each call:

- asks for a **JSON object matching a schema** (structured output) — the most
  important capability; a model that cannot do this reliably will fail stages
  with `invalid_structured_output`;
- needs solid **reasoning** over requirements and earlier artifacts;
- carries a growing prompt (later stages include earlier artifacts; build
  calls add up to `limits.max_context_chars` of source code), so it needs a
  usable **context window** (the shipped config uses `num_ctx: 32768` for the
  14B models);
- is not latency-critical — a stage taking a minute or two locally is normal.

Native tool calling is not used: the Coding Agent returns file changes as
structured output.

## Selection criteria

| Criterion | What to check | How |
|---|---|---|
| License | Is the license acceptable for your use (commercial, redistribution)? Licenses differ between sizes of the same family. | `ollama show <model>` prints the license; also the model's page on ollama.com |
| Size (parameters) | Larger models reason better and follow schemas more reliably, but need more memory and are slower. | `ollama list`; doctor shows parameter size |
| Quantization | Ollama's default tags are 4-bit (e.g. `Q4_K_M`): roughly a quarter of the full-precision size with modest quality loss. Higher-bit tags (`q8_0`, `fp16`) use much more memory. | Doctor shows quantization; pick a tag on the model's ollama.com page |
| RAM / VRAM | The model file must fit in memory with room for the context (KV cache) and your other apps. On Apple Silicon RAM and GPU memory are shared. | Doctor WARNs when the model is > 70% of total RAM |
| Context window | The model's maximum must be ≥ your `num_ctx`. Larger `num_ctx` costs memory. | Doctor shows `ctx N` (the model's maximum) |
| Structured output | Must return schema-valid JSON consistently. Ollama constrains output to the schema, but weak models still produce poor or incomplete content. | `factory doctor --probe`, then `factory evaluate` |
| Reasoning / coding | Quality of the design artifacts and of the generated code. | `factory evaluate`; then run the example project and review the artifacts and the real test results |
| Tool calling | Not needed. | — |
| Speed | Acceptable wall-clock per stage on your machine. | Probe latency; `factory history` durations |

## Starting points by RAM

Approximate download sizes are for Ollama's default 4-bit tags; confirm with
`ollama list` after pulling. "Headroom" matters: leave several GB for macOS,
your editor and the browser.

### 8 GB RAM

Tight. Only small models fit, and quality of design artifacts will be limited.

| Role | Model | Approx. size | Notes |
|---|---|---|---|
| `reasoning` + `coding` | `qwen2.5:7b` | ~4.7 GB | Apache 2.0. Close other apps; use `num_ctx: 8192` and a lower `max_context_chars` (about 12000). |
| fallback | `qwen2.5:3b` | ~1.9 GB | Smaller and weaker. Note: the 3B uses the Qwen Research license, not Apache 2.0 — check it fits your use. |

Use a single-model layout: point both profiles at `local_model`, change it to
the smaller model and drop `local_coder` (see
[model configuration](model_configuration.md#use-a-single-model-low-memory-machines));
it must keep declaring both `reasoning` and `coding`. Expect only small
projects to build successfully.

### 16 GB RAM

| Role | Model | Approx. size | Notes |
|---|---|---|---|
| `reasoning` | `qwen2.5:7b` | ~4.7 GB | Apache 2.0. Comfortable fit. |
| alternative | `llama3.1:8b` | ~4.9 GB | Llama 3.1 Community License. 128K maximum context. |
| stretch | `qwen2.5:14b` | ~9 GB download | Loaded it needs ~12 GB at 16k context, ~15 GB at 32k — too tight at 32k; use `num_ctx: 16384` and expect slower runs if other apps are open. |
| `coding` | `qwen2.5-coder:7b` | ~4.7 GB | Apache 2.0. Code-specialised; with `num_ctx: 16384` also set `max_context_chars` to about 20000; fall back to the reasoning model. |

### 32 GB RAM (e.g. Apple M1 Pro 32 GB — the reference machine)

The shipped config targets this tier with two models (user decision
2026-09-30): `qwen2.5:14b` for `reasoning` (and as the `coding` fallback) and
`qwen2.5-coder:14b` for `coding`. Other rows are optional:

| Role | Model | Approx. size | Notes |
|---|---|---|---|
| `reasoning`, `coding` fallback (shipped) | `qwen2.5:14b` | ~9 GB download | Apache 2.0. 32K maximum context. |
| `coding` (shipped) | `qwen2.5-coder:14b` | ~9 GB download | Apache 2.0. Code-specialised; `num_ctx: 32768`, temperature 0.2. Passes `factory evaluate --case code_generation`. |
| fallback (optional) | `qwen2.5:7b` | ~4.7 GB | Apache 2.0. Add as a fallback if you want one. |
| stretch | `qwen2.5:32b` | ~20 GB | Apache 2.0. Better reasoning, noticeably slower; doctor will not WARN (under 70% of 32 GB) but little memory remains for other apps and a large context. |

**Memory, measured on the reference machine:** a loaded 14B model takes
~15 GB with `num_ctx: 32768` and ~12 GB with 16k — much more than its ~9 GB
download, because of the KV cache. Two 14B models loaded together therefore do
not fit comfortably in 32 GB. Start Ollama with `OLLAMA_MAX_LOADED_MODELS=1`
so only one is in memory; the workflow then pays a reload of a few seconds
when it switches between design and build agents (see
[local model setup](local_model_setup.md#2-start-the-server)).

### 64 GB RAM or more

| Role | Model | Approx. size | Notes |
|---|---|---|---|
| `reasoning` | `qwen2.5:32b` | ~20 GB | Apache 2.0. Good step up for design quality. |
| fallback | `qwen2.5:14b` | ~9 GB | Apache 2.0. |
| `coding` | `qwen2.5-coder:32b` | ~20 GB | Apache 2.0. Noticeably better code; slower per fix round. |
| stretch | `qwen2.5:72b` | ~47 GB | Qwen license (not Apache 2.0) — review it. Slow on most laptops. |

With this much memory you can keep `num_ctx` at the model's maximum (32768
for qwen2.5, as shipped) even for 32B models, and raise `max_context_chars` if
larger projects need more code per call.

## The coding role

The `coding` profile serves three agents: the Coding Agent (writes and fixes
files), the Testing Agent (explains failing commands) and the Code Review
Agent (reviews the diff). Their output is judged by real programs — the plan's
lint, build and test commands — not by the model.

- **Code model or general model?** `qwen2.5-coder` (7B/14B/32B) is trained for
  code and usually produces code that builds and passes tests in fewer fix
  rounds than general `qwen2.5` of the same size. General `qwen2.5` is a
  reasonable fallback and is better at the prose parts (review explanations,
  failure analysis). The shipped config uses `qwen2.5-coder:14b` as the
  `coding` profile's preferred model with general `qwen2.5:14b` as fallback
  (see
  [model configuration](model_configuration.md#add-a-fallback-or-a-per-role-model)).
  Any model the `coding` profile uses, including fallbacks, must declare the
  `coding` capability.
- **Size.** 7B coders handle small, single-language projects; 14B is the
  practical minimum for a multi-file web app; 32B helps with larger code bases
  if you have the memory.
- **Context costs RAM.** Build calls send requirements, architecture, the plan
  and up to `limits.max_context_chars` (40 000) characters of code. That needs
  `num_ctx: 32768` for 14B models, and the KV cache for a 32K context adds
  several GB on top of the model file (measured: ~15 GB in total at 32k,
  ~12 GB at 16k for a 14B model). On less memory, lower `num_ctx` **and**
  `max_context_chars` together.
- **Expect several fix rounds.** Local models often need more than one attempt
  to get a build or test green. Each bug-fixing round tries up to
  `limits.fix_candidates` (3) fixes against the real commands and keeps the
  first that passes; `limits.max_debug_iterations` (5) bounds the rounds per
  failure; `max_review_rounds` (2) bounds review and security fix rounds. The
  same failure three times in a row stops the project with `No progress: …`.
  Many exhausted budgets mean the model is too weak for the project.
- **Known limitation: small local models miss logic bugs.** In the first live
  full build (a todo app, 2026-09-30), `qwen2.5:14b` alone for every agent
  reached working code only with human hints (`factory retry --hint`). It
  could not find two logic bugs on its own: JSON object keys come back as
  strings after loading (so integer ids no longer matched), and new ids reused
  a deleted item's id (`count + 1`). The Testing Agent tended to blame the
  (correct) tests. These findings led to the coder model for `coding`, the
  pitfalls checklist in the coding prompt, fix candidates, stuck detection and
  `--hint`. Expect a 14B local model to need an occasional human hint on
  logic bugs; a larger or hosted `coding` model needs fewer.
- **Judge by real results.** Compare models on the same small project: did it
  reach `WAITING_FOR_RELEASE_APPROVAL`, how many `BUG_FIXING` rounds did
  `factory history` show, what do `test-results.md` and `review.md` say, and
  does the code in `source/` actually run? The doctor probe only proves the
  model can return JSON; `factory evaluate`'s `code_generation` case actually
  runs generated tests, but only on one tiny module.

## Context window notes

- The qwen2.5 family reports a 32K maximum context in Ollama; `llama3.1`
  reports 128K. `factory doctor` shows the real value as `ctx N`.
- Ollama's own default context is much smaller than the maximum, which is why
  the config sets `num_ctx`. Too small a context silently truncates prompts
  or produces `context_length_exceeded`; too large wastes memory.
- `models.<key>.context_window` in the config is documentation only; set the
  effective context with `options.num_ctx`.

## Verify with `factory evaluate`

Verify a model with `factory evaluate` before relying on it. It is the
spec §68.17 evaluation — not a general benchmark: each case runs a **real
factory agent** with its real prompts, schemas and model routing (the
configured `reasoning` / `coding` profiles) on a small fixed task, and checks
the answer with deterministic code, not the model's opinion
(`src/software_factory/core/model_evaluation.py`).

```bash
uv run factory evaluate
```

```bash
uv run factory evaluate --case code_generation --case technology_fit
```

`--case NAME` is repeatable; an unknown name fails with
`Unknown evaluation case(s): …` and lists the valid ones.

| Case | Agent (profile) | Passes when |
|---|---|---|
| `requirement_extraction` | Requirement (`reasoning`) | From a clear notes-tool request: ≥ 3 functional requirements, and the "listing within one second" requirement captured as a non-functional requirement |
| `ambiguity_detection` | Requirement (`reasoning`) | For the vague request "Build an app for managing things.": at least one **required** clarification question |
| `architecture_reasoning` | Architecture (`reasoning`) | ≥ 2 components, FR-1, FR-2 and FR-3 each mapped to a component, and ≥ 1 alternative considered |
| `technology_fit` | Technology Selection (`reasoning`) | The environment says only `python` and `git` are installed (node, npm, docker, java are not): no choice mentions Node, React, Java, Spring, Docker, .NET or Go, and every choice has a rationale |
| `code_generation` | Coding (`coding`) | The agent writes a temperature-conversion module plus unittest tests into a temporary directory; `python -m unittest -v` must run ≥ 1 test and exit 0 |

Each line prints `PASS`/`FAIL`, the case, its profile, duration and a detail
(e.g. `generated tests pass (Ran N tests in …s)`). A model error or
invalid structured output counts as a FAIL (`error: …`). The summary
`N/5 passed` is followed by the report folder; the full report is saved as
`<workspace>/evaluations/evaluation-<timestamp>.json` (per case: capability,
passed, duration, detail, model). The command exits with code 1 if any case
fails, so it can gate a script.

Reference result: `qwen2.5:14b` on an Apple M1 Pro 32 GB passes all 5 cases
in about 3.5 minutes; `code_generation` passed in 3 of 3 runs. With the
shipped two-model config, `code_generation` runs on `qwen2.5-coder:14b` and
passes. Model output
varies between runs, so re-run a single failing case before concluding a
model is too weak. Troubleshooting FAIL lines:
[troubleshooting](../setup/troubleshooting.md#factory-evaluate-reports-fail).

Passing all five does not guarantee a good larger project — it shows the
model can do each kind of task the factory asks of it.

## Verify your choice

1. Update `models.<key>.model` (and `capabilities`) in `config/factory.yaml`.
2. Pull the models (Ollama; for another server, load/download the model there):

   ```bash
   ollama pull <model>
   ```

3. Check install, size vs RAM, quantization and context:

   ```bash
   uv run factory doctor
   ```

4. Check live structured output for every profile:

   ```bash
   uv run factory doctor --probe
   ```

5. Evaluate the model on the five factory tasks:

   ```bash
   uv run factory evaluate
   ```

6. Run the example project and review the Markdown artifacts — at least the
   requirements and architecture stages for a `reasoning` model, through to
   release approval for a `coding` model:

   ```bash
   uv run factory create "Model Trial" --requirements-file examples/employee-management/requirements.md
   ```

   ```bash
   uv run factory start model-trial
   ```

7. Inspect `factory history model-trial` — frequent repair attempts
   (`invalid_output`), fallbacks, timeouts or many fix rounds mean the model
   is too weak or too big for the machine.

The probe is a smoke test: it only proves the model returns schema-valid
JSON. `factory evaluate` is the check of whether the model can do the
factory's actual tasks.

## Related documents

- [Local model setup](local_model_setup.md)
- [Model configuration](model_configuration.md)
- [Setup guide](../setup/setup_guide.md)
- [Troubleshooting](../setup/troubleshooting.md)
