# Local model setup

The factory talks to models through a provider adapter. Two adapters exist:
**Ollama** (provider type `ollama`), a local model server with an HTTP API on
`http://localhost:11434`, which the shipped config uses; and
**OpenAI-compatible** (provider type `openai_compatible`), for other local
servers such as LM Studio, llama.cpp server or vLLM, and for cloud APIs (see
[other local servers and cloud APIs](#other-local-servers-and-cloud-apis)).
This page covers installing Ollama, downloading the two configured models and
verifying it with `factory doctor`, `factory doctor --probe` and
`factory evaluate`.

Which models to use is covered in [model selection](model_selection.md); how
the config maps agents to models is covered in
[model configuration](model_configuration.md).

> Automatic hardware detection, model download and registration (the
> onboarding flow in spec §68.1) is **Planned — not implemented yet**. You
> install Ollama and pull models yourself; `factory doctor` then verifies them.

## 1. Install Ollama

### macOS

Either install with Homebrew:

```bash
brew install ollama
```

or download the macOS app from [ollama.com](https://ollama.com/download) and
move it to Applications. The app runs the server in the background (menu-bar
icon) and installs the `ollama` command.

Check the installation:

```bash
ollama --version
```

On Apple Silicon (M1/M2/M3/M4) Ollama uses the GPU through Metal and the
machine's unified memory, so the model has to fit in RAM together with
everything else you are running.

### Linux

Follow the instructions on [ollama.com](https://ollama.com/download). The
installer sets up a system service.

## 2. Start the server

A Homebrew install does **not** start the server by itself, and it is not a
login service until you make it one. Start it in its own terminal (it stops
when you close that terminal):

```bash
OLLAMA_MAX_LOADED_MODELS=1 ollama serve
```

or run it as a background service that also starts at every login:

```bash
brew services start ollama
```

If you use the macOS app, launching the app starts the server; do not also run
`ollama serve` (the port would already be in use).

**Keep only one model in memory.** The shipped config uses two 14B models
(one for reasoning, one for coding) and the workflow switches between them.
Set `OLLAMA_MAX_LOADED_MODELS=1` for the server so Ollama unloads one model
before loading the other; switching then costs a reload of a few seconds
instead of swapping. Measured on the reference machine (M1 Pro 32 GB): a 14B
model needs ~15 GB with `num_ctx: 32768` and ~12 GB with 16k context, so two
at once do not fit next to everything else. When the server runs as the macOS
app or a background service, the variable has to reach that process (for the
app, Ollama's documentation uses `launchctl setenv OLLAMA_MAX_LOADED_MODELS 1`
and a restart of the app); the simplest reliable way is the `ollama serve`
command above. `ollama ps` shows what is loaded.

Verify the server answers — this is the same endpoint the factory's health
check uses:

```bash
curl http://localhost:11434/api/tags
```

If Ollama runs elsewhere (another port or machine), set
`providers.ollama.base_url` in `config/factory.yaml`.

## 3. Download the models

The shipped `config/factory.yaml` references two models (user decision
2026-09-30):

| Config key | Ollama model | Used by | Needed? | Approx. download |
|---|---|---|---|---|
| `local_model` | `qwen2.5:14b` | preferred for `reasoning` (Requirement, Architecture, Technology Selection, Planning, Impact Analysis agents); fallback for `coding` | **Required** | ~9 GB |
| `local_coder` | `qwen2.5-coder:14b` (`num_ctx` 32768) | preferred for `coding` (Coding, Testing, Code Review agents) | Recommended (without it `coding` falls back to `qwen2.5:14b`) | ~9 GB |

Sizes are the default (4-bit) Ollama tags; `ollama list` shows the exact size
on your machine. Download size is not memory use: loaded with a 32k context a
14B model takes ~15 GB (see [Start the server](#2-start-the-server)).

```bash
ollama pull qwen2.5:14b
```

```bash
ollama pull qwen2.5-coder:14b
```

Optional extra (not in the shipped config; add it under `models` and a profile
first — see
[model configuration](model_configuration.md#add-a-fallback-or-a-per-role-model)):

- `qwen2.5:7b` (~4.7 GB) — a smaller model you can use as a fallback, or as
  the only model on a low-memory machine.

A model you have pulled but no longer reference (e.g. an earlier `qwen2.5:7b`)
only uses disk space; remove it with `ollama rm qwen2.5:7b`.

List what is installed:

```bash
ollama list
```

The name in `models.<key>.model` must match a name in this list. The factory
also accepts a name without a tag if the `:latest` tag is installed (e.g.
`llama3.1` matches `llama3.1:latest`).

`factory doctor` checks every configured model. A missing model is always a
**WARN**; what matters is the `Profile:` rows, which FAIL only when a profile
has **no** installed model at all. With the shipped config, pulling both
models makes every model and profile row PASS. With only `qwen2.5:14b`,
`Model: local_coder` is WARN and `Profile: coding` is WARN (fallback); without
`qwen2.5:14b` the `reasoning` profile FAILs.

On a machine with less RAM, choose smaller models first
([model selection](model_selection.md)) and change the `model:` values.

### Optional: try a model by hand

```bash
ollama run qwen2.5:14b "Reply with the JSON object {\"ok\": true}"
```

The first request after a pull (or after the model was unloaded) is slow
because the model is loaded into memory.

## 4. Verify with `factory doctor`

```bash
uv run factory doctor
```

For each enabled provider, doctor calls `GET /api/tags`:

- `Provider: ollama PASS — N model(s) installed`, or
- `Provider: ollama FAIL — Cannot reach Ollama at http://localhost:11434 — start it with ollama serve`.

For each configured model on a reachable provider it reports:

- whether it is installed; if not, a `WARN … is not installed (<usage>)`
  with an `ollama pull` hint, where `<usage>` is e.g.
  `preferred for reasoning; fallback for coding` (the shipped `local_model`),
  `preferred for coding` (the shipped `local_coder`) or `not used by any profile`,
- parameter size and quantization (from Ollama's model list),
- the model's maximum context length (from `POST /api/show`),
- the capabilities **declared** in the config,
- `WARN` if the model's size is more than 70% of the machine's total RAM.

Then one row per profile the agents use (`reasoning`, `coding`):

- `PASS — uses <model>`: the preferred model is installed;
- `WARN — preferred model missing; will use fallback <model>` (only if you
  configured `fallback_models`);
- `FAIL — no installed model` (pull one of the listed models) or
  `FAIL — not configured`.

Example rows:

```text
Model: local_model       PASS   qwen2.5:14b · 14.8B · Q4_K_M · ctx 32768 · caps: reasoning, coding, structured_output
Model: local_coder       PASS   qwen2.5-coder:14b · 14.8B · Q4_K_M · ctx 32768 · caps: coding, structured_output
Profile: reasoning       PASS   uses qwen2.5:14b
Profile: coding          PASS   uses qwen2.5-coder:14b
```

If the provider is unreachable, every model row is `SKIP` (`provider
unreachable`) — fix the server first.

## 5. Verify the models actually work: `--probe`

The checks above only prove the model is installed. The probe sends a tiny
structured-output request (`What is 2 + 3?`, answer as JSON with an integer
field) through the real router for the `reasoning` and `coding` profiles:

```bash
uv run factory doctor --probe
```

```text
Structured output (reasoning)   PASS   qwen2.5:14b answered 5 in … ms
Structured output (coding)      PASS   qwen2.5-coder:14b answered 5 in … ms
```

- `PASS` — the model returned schema-valid JSON with the right answer.
- `WARN` — valid JSON, wrong answer; consider a stronger model.
- `FAIL` — the request failed (timeout, invalid JSON after repair attempts,
  model unavailable…); the detail shows the error.
- `SKIP` — the profile has no model with `structured_output`.

The probe uses no retries, but it does use fallback (if you configured any), so
the detail names the model that actually answered. If it is not the preferred
model, the preferred one failed or is not pulled; check `ollama list` and the
timeout. With the shipped config the reasoning probe names `qwen2.5:14b` and
the coding probe `qwen2.5-coder:14b`. The first probe after a model switch is
slower (the other model is unloaded and this one loaded).

The probe does not run if any provider is unreachable. Tool calling is not
probed (no agent uses native tool calls).

Always run `--probe` after pulling a new model or editing models/profiles.

## 6. Check the model can do the factory's work: `factory evaluate`

The probe only proves the model answers in JSON. `factory evaluate` runs the
real Requirement, Architecture, Technology Selection and Coding agents on five
small fixed tasks and checks the answers with code — the code-generation case
actually runs the generated unit tests:

```bash
uv run factory evaluate
```

With `qwen2.5:14b` on an M1 Pro 32 GB all five cases pass in about
3.5 minutes; with the shipped two-model config `code_generation` runs on
`qwen2.5-coder:14b` and passes too (`uv run factory evaluate --case code_generation`). Any `FAIL` makes the command exit with code 1. What each case
checks and how to read the report:
[model selection](model_selection.md#verify-with-factory-evaluate).

## 7. Useful Ollama commands

| Command | Purpose |
|---|---|
| `ollama list` | Installed models and their sizes |
| `ollama ps` | Models currently loaded in memory |
| `ollama show <model>` | Parameters, quantization, context length, license |
| `ollama rm <model>` | Delete a model to free disk space |
| `ollama stop <model>` | Unload a model from memory |

## Other local servers and cloud APIs

Ollama is not required. Any server that speaks the OpenAI Chat Completions
format works through provider type `openai_compatible` — a config-only
change, no code change:

- **Local, no key:** LM Studio (start its local server; default
  `http://localhost:1234/v1`), llama.cpp server, vLLM, or Ollama's own
  OpenAI-compatible endpoint `http://localhost:11434/v1` (verified live on
  2026-09-29 with `qwen2.5:14b`).
- **Cloud:** DeepSeek, OpenAI, Groq, OpenRouter, … — the provider names an
  environment variable (`api_key_env`) holding the key; the key is never put
  in the config file.

`config/factory.yaml` contains commented `lmstudio` and `deepseek` examples.
Enable one, add a model that uses it, point the profiles at that model, then
run `factory doctor --probe` and `factory evaluate`. Doctor checks these
providers with `GET {base_url}/models`. Full YAML for LM Studio, DeepSeek and
a mixed setup (reasoning in the cloud, coding local):
[model configuration](model_configuration.md#openai-compatible-servers-and-cloud-apis).

## Related documents

- [Model selection](model_selection.md)
- [Model configuration](model_configuration.md)
- [Setup guide](../setup/setup_guide.md)
- [Troubleshooting](../setup/troubleshooting.md)
- [LLM architecture](../architecture/llm_architecture.md)
