# Configuration reference

All factory behaviour that may differ between machines — workspace location,
model server, model names, fallback order, retry limits, shell permissions — is
set in one YAML file, `config/factory.yaml`. Model names are configuration,
never code.

The schema is defined in `src/software_factory/config/settings.py`
(Pydantic models). Unknown keys are ignored; invalid values or broken
references stop every command with `Invalid factory configuration: ...`.

> **Keep this file current.** Adding, removing or changing a config key means
> updating this page in the same change (spec §73.7).

## Where the config file is found

The CLI looks for the file in this order and uses the first match:

1. `--config PATH` / `-c PATH` — a global option, so it goes **before** the
   command: `factory -c other.yaml doctor`.
2. The `FACTORY_CONFIG` environment variable (from the shell or from `.env`
   in the current directory — see [below](#the-env-file)).
3. `./config/factory.yaml` relative to the **current working directory**.

If none applies you get `No factory configuration found`.

```bash
uv run factory -c config/factory.yaml doctor
```

```bash
FACTORY_CONFIG=/abs/path/factory.yaml uv run factory list
```

## Relative paths

Relative paths **inside** the file (`factory.workspace`,
`factory.prompts_dir`) resolve against the directory that **contains the
config file**, not the shell's current directory. That is why the shipped
`config/factory.yaml` uses `../factory-workspace` and `../prompts`: they point
at the repository root. `~` is expanded; absolute paths are used as-is.

## Secrets

Secrets never go in this file. A provider names an environment variable with
`api_key_env`; the value is read from the environment only when that provider
is actually used, and is never written to config, workflow state, artifacts,
history or logs. Every artifact write and every log line is passed through
secret redaction (known key shapes such as `sk-…`, `AKIA…`, `ghp_…`, `AIza…`,
bearer tokens, `api_key=`/`password=` pairs, private-key blocks, plus the value
of any environment variable whose name contains `KEY`, `TOKEN`, `SECRET`,
`PASSWORD` or `CREDENTIAL`). The same name list (`SECRET_ENV_HINTS` in
`observability/redaction.py`) is used to scrub such variables from the
environment of commands run by the shell tool.

## The `.env` file

Before looking for the config file, every CLI command loads `.env` from the
**current working directory** (not from the config file's directory):

- one `KEY=value` per line; `export KEY=value` is also accepted;
- surrounding single or double quotes are stripped from the value;
- blank lines, `#` comment lines and lines without `=` are ignored, as are
  entries with an empty value;
- variables already set in the shell **win** — `.env` never overrides them;
- values are never logged.

Because `.env` is loaded first, a `FACTORY_CONFIG` entry in it is honoured.
This is also the usual place for API keys of `openai_compatible` providers
(e.g. `DEEPSEEK_API_KEY=...`); the key is read from the environment each time
that provider makes a request.

## Full example

The shipped file (comments shortened):

```yaml
factory:
  environment: local
  workspace: ../factory-workspace
  prompts_dir: ../prompts

orchestrator:
  type: langgraph

providers:
  ollama:
    type: ollama
    enabled: true
    base_url: http://localhost:11434
    timeout_seconds: 600

  # Commented examples (not active): any OpenAI-compatible server or API.
  # lmstudio:                            # local, no key
  #   type: openai_compatible
  #   base_url: http://localhost:1234/v1
  #
  # deepseek:                            # cloud; key read from the environment
  #   type: openai_compatible
  #   base_url: https://api.deepseek.com/v1
  #   api_key_env: DEEPSEEK_API_KEY
  #   options:
  #     structured_output: json_object   # json_schema (default) | json_object | prompt

# Start Ollama with OLLAMA_MAX_LOADED_MODELS=1 so only one model is in memory.
models:
  local_model:
    provider: ollama
    model: qwen2.5:14b               # ~9 GB; use qwen2.5:7b on machines with < 16 GB RAM
    capabilities: [reasoning, coding, structured_output]
    context_window: 32768
    options:
      num_ctx: 32768

  local_coder:
    provider: ollama
    model: qwen2.5-coder:14b         # trained on code; ~9 GB
    capabilities: [coding, structured_output]
    context_window: 32768
    options:
      num_ctx: 32768

profiles:
  reasoning:                         # Requirement, Architecture, Technology, Planning, Impact agents
    preferred_model: local_model
    required_capabilities: [reasoning]
    temperature: 0.1
  coding:                            # Coding, Testing and Code Review agents
    preferred_model: local_coder
    fallback_models: [local_model]   # qwen2.5:14b if the coder model is unavailable
    required_capabilities: [coding]
    temperature: 0.2                 # a little variety helps when several fixes are tried

persistence:
  type: sqlite

limits:
  max_agent_retries: 2
  max_structured_output_repairs: 2
  max_clarification_rounds: 5
  max_debug_iterations: 5
  max_review_rounds: 2
  fix_candidates: 3
  max_context_chars: 40000
  retry_backoff_seconds: 2

security:
  shell_enabled: true
  network_enabled: false
  # isolate_project_environment: true   (default; not in the shipped file)
  allowed_commands: [git, python, python3, pip, pytest, ruff, uv, node, npm, npx]
  command_timeout_seconds: 600

logging:
  level: INFO
```

## Section reference

Defaults below are the values used when a key is omitted (they can differ from
the shipped file).

### `factory`

| Key | Default | Meaning |
|---|---|---|
| `environment` | `local` | Free-form label. Currently informational only (included in the config fingerprint). |
| `workspace` | `./factory-workspace` | Root for `factory.db`, `logs/`, `projects/` and `evaluations/` (`factory evaluate` reports). Created automatically. |
| `prompts_dir` | `./prompts` | Folder with one sub-folder per agent (`requirement`, `architecture`, `technology_selection`, `implementation_planning`, `coding`, `testing`, `code_review`), each containing `system.md` and `task.md`. |

### `orchestrator`

| Key | Default | Meaning |
|---|---|---|
| `type` | `langgraph` | Only `langgraph` is accepted. |

### `persistence`

| Key | Default | Meaning |
|---|---|---|
| `type` | `sqlite` | Only `sqlite` is accepted: `<workspace>/factory.db` plus one checkpoint database per project. |

### `providers.<name>`

A provider is a model server or API. `<name>` is your label; models refer to
it by that label.

| Key | Default | Meaning |
|---|---|---|
| `type` | (required) | Adapter type: `ollama` (native Ollama API) or `openai_compatible` (any Chat Completions server: LM Studio, llama.cpp server, vLLM, Ollama's `/v1`, DeepSeek, OpenAI, Groq, OpenRouter, …). Any other value fails with `Provider '<name>' has type '<type>', which is not implemented`. |
| `enabled` | `true` | Disabled providers are skipped by `factory doctor`, and their models are never selected by the router (not even as fallbacks). |
| `base_url` | adapter default | For Ollama: `http://localhost:11434`. For `openai_compatible`: **required** — the part before `/chat/completions`, e.g. `http://localhost:1234/v1` (LM Studio) or `https://api.deepseek.com/v1`. |
| `api_key_env` | none | Name of the environment variable holding the API key (`openai_compatible` only; the Ollama adapter ignores it). Omit for local servers without auth. Read at call time; if set here but missing from the environment, the call fails with `Provider '<name>' requires environment variable <VAR>`. |
| `timeout_seconds` | `300` | Per-request timeout for generation. The shipped file uses `600` because local models can be slow on large prompts. (Health and metadata calls use fixed 5 s / 10 s timeouts.) |
| `options` | `{}` | Only `structured_output` is read, and only by `openai_compatible`: `json_schema` (default), `json_object` or `prompt` — how a JSON answer is requested (see [model configuration](../models/model_configuration.md#provider-settings)). Other keys are ignored and not sent; put request parameters on the model instead. The Ollama adapter ignores provider `options`. |

### `models.<key>`

A model entry binds a concrete model name to a provider and declares what it
can do. `<key>` is the logical name profiles refer to.

| Key | Default | Meaning |
|---|---|---|
| `provider` | (required) | A key under `providers`. Unknown → `model '<key>' references unknown provider '<name>'`. |
| `model` | (required) | The provider's model name, e.g. `qwen2.5:14b` for Ollama (as shown by `ollama list`), or an `id` from `GET {base_url}/models` for an OpenAI-compatible server (e.g. `deepseek-chat`). |
| `capabilities` | `[]` | Declared capabilities (see below). The router uses them to pick and filter models. They are **declared, not detected**. |
| `context_window` | none | Documentation of the model's context size. Informational; not enforced. `factory doctor` reports the real maximum from Ollama (or from an OpenAI-compatible server's `/models` entry, if it lists `context_length` / `max_context_length`). |
| `options` | `{}` | Passed to the provider. For Ollama these go into the request's `options` object (e.g. `num_ctx`, `top_p`), **except** `think` and `keep_alive`, which are sent as top-level request fields. For `openai_compatible` they are merged into the top level of the request body (e.g. `max_tokens`, `top_p`). `temperature` is always set from the profile. |

Capability names: `reasoning`, `coding`, `fast`, `structured_output`,
`tool_calling`, `vision`, `long_context`, `streaming`.

### `profiles.<name>`

Agents never name a model; they ask for a **profile**:

| Profile | Used by | Doctor |
|---|---|---|
| `reasoning` | Requirement, Architecture, Technology Selection, Implementation Planning agents | **Must exist** (Configuration FAIL otherwise); `Profile: reasoning` row |
| `coding` | Coding, Testing and Code Review agents (build phase) | **Must exist** (`Profile: coding FAIL — not configured` otherwise) |

Other profile names are accepted but not used by any agent or checked by doctor.

A profile only works if at least one of its models (preferred or fallback) is
installed; doctor reports that in the `Profile: <name>` rows. The shipped config
(user decision 2026-09-30) uses two models: `local_model` (`qwen2.5:14b`) for the
`reasoning` profile, and `local_coder` (`qwen2.5-coder:14b`) for the `coding`
profile with `local_model` as fallback. `local_model` still declares `coding`, so
the coding profile keeps working if only `qwen2.5:14b` is pulled. Pull both
models, and start Ollama with `OLLAMA_MAX_LOADED_MODELS=1` so only one 14B model
is in memory at a time (measured ~15 GB at 32k context, ~12 GB at 16k); see
[local model setup](../models/local_model_setup.md).

To give a profile a different model, or a fallback, add another entry under
`models` and point the profile at it (the shipped `local_coder` entry above is
exactly this pattern).

| Key | Default | Meaning |
|---|---|---|
| `preferred_model` | (required) | Model key tried first. |
| `fallback_models` | `[]` | Model keys tried next, in order. |
| `required_capabilities` | `[]` | Every candidate model must declare all of these. |
| `temperature` | `0.0` | Sampling temperature sent with each request. |

Unknown model keys → `profile '<name>' references unknown model '<key>'`.
Routing, retry and fallback rules are explained in
[model configuration](../models/model_configuration.md).

### `limits`

| Key | Default | Range | Meaning |
|---|---|---|---|
| `max_agent_retries` | `2` | 0–10 | Extra attempts **per model** after a retryable error (timeout, rate limit, provider unavailable, network, malformed response). |
| `max_structured_output_repairs` | `2` | 0–5 | Times the model is shown its validation error and asked to correct its JSON before the attempt counts as failed. |
| `max_clarification_rounds` | `5` | 1–20 | Maximum rounds of Requirement Agent questions; after that, remaining questions are recorded as open questions in the requirements (plus a warning) instead of blocking. |
| `max_debug_iterations` | `5` | 0–20 | Automatic fix attempts (Testing Agent analysis + Coding Agent fix) for a failing build, test or final validation before the stage fails and waits for you. One round counts once, however many `fix_candidates` it tried. The counter resets when the tests pass and on `factory retry`. Independently, the stage fails early with "No progress: …" when the same failure comes back 3 times in a row. |
| `fix_candidates` | `3` | 1–5 | Fixes the Coding Agent proposes, one after another, in one bug-fixing round for a failed build or test. Each is applied and checked with the real format/lint/build/test commands (plus setup if manifests changed); the first that passes is kept, failing ones are rolled back and shown to the next candidate. Review, security, validation and release-feedback fixes always use 1. The shipped file sets `3`. Each candidate costs one LLM call and one command run. |
| `max_review_rounds` | `2` | 0–10 | Fix rounds for blocking (critical/high) code-review findings, and separately for blocking security findings. After the limit, open review findings become a warning for the release decision; open security findings fail the stage. |
| `max_context_chars` | `40000` | 2 000–500 000 | Characters of source code given to the Coding, Testing and Code Review agents per call (most relevant files first; the review diff is truncated to this length). Larger values need a larger `num_ctx`. |
| `retry_backoff_seconds` | `1.0` | ≥ 0 | Wait before retry *n* is `retry_backoff_seconds × n`. |

### `security`

Used by the shell tool (`src/software_factory/tools/shell.py`), which runs the
approved plan's project commands, and by the git tool.

| Key | Default | Meaning |
|---|---|---|
| `shell_enabled` | `true` | If `false`, every shell command is refused — including git, so the build phase cannot run. |
| `isolate_project_environment` | `true` | Run project commands without the factory's own Python virtual environment: its `bin` directory is removed from `PATH` and `VIRTUAL_ENV` / `PYTHONHOME` are unset. The "installed" check for plan commands and the environment facts given to the Technology Selection and Planning agents use the same lookup. Set `false` only if a project should deliberately use the factory's tools. |
| `network_enabled` | `false` | Reserved; **not enforced** by any tool yet. Setup commands such as `npm install` or `pip install` download packages and need network access. |
| `allowed_commands` | `git, python, python3, pip, pytest, ruff, uv, node, npm, npx` | Programs project commands may start with. `sh`, `bash`, `zsh`, `sudo`, `su`, `rm`, `curl` and `wget` are always refused, even if listed. The build phase stops early if a plan command's program is not listed or not installed (installed = found on the project's `PATH`, see `isolate_project_environment`). The Technology Selection and Implementation Planning agents are told which listed programs are installed. |
| `command_timeout_seconds` | `600` | Timeout per command. A timed-out command counts as failed. Raise it if dependency installs or test suites are slow. |

See [security architecture](../architecture/security_architecture.md) and the
[shell tool](../tools/shell_tool.md).

### `logging`

| Key | Default | Meaning |
|---|---|---|
| `level` | `INFO` | Level for `<workspace>/logs/factory.log` (JSON lines, one object per record, secrets redacted). Nothing is logged to the terminal. |

## Validating a change

After editing the file:

```bash
uv run factory doctor
```

The Configuration row shows which file was loaded and a 12-character
fingerprint of the effective configuration, so you can confirm the edit was
picked up. Use `--probe` after changing models, providers or profiles, then
`uv run factory evaluate` to check the new model on real factory tasks (see
[model selection](../models/model_selection.md#verify-with-factory-evaluate)).

## Related documents

- [Setup guide](setup_guide.md)
- [Model configuration](../models/model_configuration.md)
- [Model selection](../models/model_selection.md)
- [Troubleshooting](troubleshooting.md)
