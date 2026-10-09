# Model configuration

Agents never name a model. They ask the LLM layer for a **profile**
(`reasoning`, `coding`, …). The config maps each profile to concrete models on
concrete providers. Changing models, fallback order or provider is therefore a
config change, not a code change.

```text
Agent ──(profile: reasoning)──▶ ModelRouter ──▶ ModelRegistry (config)
                                    │               providers / models / profiles
                                    ▼
                            provider adapter ──▶ ollama:            Ollama HTTP API
                                              └──▶ openai_compatible: any Chat Completions server
                                                                      (LM Studio, vLLM, DeepSeek, OpenAI, …)
```

Code: `src/software_factory/llm/registry.py` (what is configured),
`src/software_factory/llm/router.py` (selection, retry, fallback, structured
output), `src/software_factory/llm/providers/` (adapters). Background:
[LLM architecture](../architecture/llm_architecture.md).

## The three sections

### Providers

```yaml
providers:
  ollama:
    type: ollama
    enabled: true
    base_url: http://localhost:11434
    timeout_seconds: 600
```

`type` selects the adapter. Two types are implemented: `ollama` (native
Ollama API) and `openai_compatible` (any server or API that speaks the Chat
Completions format — see
[OpenAI-compatible servers and cloud APIs](#openai-compatible-servers-and-cloud-apis)).
Any other type fails with
`Provider '<name>' has type '<type>', which is not implemented`. A provider is
created lazily the first time one of its models is used, so a disabled or
unused provider never needs its API key.

### Models

```yaml
models:
  local_model:
    provider: ollama
    model: qwen2.5:14b
    capabilities: [reasoning, coding, structured_output]
    context_window: 32768
    options:
      num_ctx: 32768

  local_coder:
    provider: ollama
    model: qwen2.5-coder:14b
    capabilities: [coding, structured_output]
    context_window: 32768
    options:
      num_ctx: 32768
```

The shipped config has these two model entries (user decision 2026-09-30):
`local_model` serves the reasoning agents and is the coding fallback;
`local_coder` serves the coding agents. `local_model` keeps `coding` in its
capabilities so it can be that fallback.

- `model` — the name as the provider knows it (`ollama list`; for an
  OpenAI-compatible server, an `id` from `GET {base_url}/models`).
- `capabilities` — what you **declare** the model can do. The router trusts
  this list; it does not detect capabilities. Verify structured output with
  `factory doctor --probe`, and the model's real task quality with
  `factory evaluate` ([model selection](model_selection.md#verify-with-factory-evaluate)).
- `context_window` — informational only (not enforced). Doctor reports the
  real maximum from Ollama (or from the `/models` entry of an
  OpenAI-compatible server, when it lists one).
- `options` — for an `ollama` model, sent in the request's `options` object
  (e.g. `num_ctx`, `top_p`, `seed`, `num_predict`); two keys are different:
  `think` and `keep_alive` are sent as **top-level** request fields, as
  Ollama's API expects. For an `openai_compatible` model, the options are
  merged into the top level of the request body (e.g. `max_tokens`, `top_p`,
  `seed`) — Ollama-only keys such as `num_ctx` do not apply there. The
  profile's `temperature` always overrides an `options.temperature`.

#### Why set `num_ctx`

Ollama runs a model with its own default context size, which is much smaller
than the model's maximum. Prompts for later stages (architecture, plan)
include earlier artifacts, and build-phase prompts add source code (up to
`limits.max_context_chars`, default 40 000 characters — very roughly 10 000
tokens) plus requirements, architecture and the plan. The shipped config
therefore sets `num_ctx: 32768` on both models. A
larger `num_ctx` uses more memory (the KV cache grows with it; measured for a
14B model: ~15 GB loaded at 32k, ~12 GB at 16k); keep it at or
below the `ctx` value `factory doctor` reports. If you lower `num_ctx` on a
model the `coding` profile uses, lower `max_context_chars` too.

### Profiles

```yaml
profiles:
  reasoning:
    preferred_model: local_model
    required_capabilities: [reasoning]
    temperature: 0.1
  coding:
    preferred_model: local_coder
    fallback_models: [local_model]
    required_capabilities: [coding]
    temperature: 0.2
```

`reasoning` uses `qwen2.5:14b` without fallbacks; `coding` prefers
`qwen2.5-coder:14b` and falls back to `qwen2.5:14b`. The coding temperature is
0.2 rather than 0.1: when a bug-fixing round asks for several fix candidates
(`limits.fix_candidates`), a little variety makes the candidates differ. See
[add a fallback or a per-role model](#add-a-fallback-or-a-per-role-model) for
the pattern.

**Run Ollama with `OLLAMA_MAX_LOADED_MODELS=1`.** The workflow alternates
between the two models; with this setting Ollama keeps only one in memory and
reloads on a switch (seconds) instead of holding ~30 GB. See
[local model setup](local_model_setup.md#2-start-the-server).

| Profile | Used by | Status |
|---|---|---|
| `reasoning` | Requirement, Architecture, Technology Selection, Implementation Planning, Impact Analysis agents | **Required** — doctor fails without it |
| `coding` | Coding, Testing and Code Review agents (build phase) | **Required** — `Profile: coding FAIL — not configured` without it |

`factory doctor` shows one `Profile: <name>` row for `reasoning` and `coding`:
PASS when the preferred model is installed, WARN when only a fallback is, FAIL
when none is. With the shipped config and both models pulled the rows are
`Profile: reasoning PASS uses qwen2.5:14b` and `Profile: coding PASS uses
qwen2.5-coder:14b`. Without the coder model, `coding` is WARN (fallback to
`qwen2.5:14b`); without `qwen2.5:14b`, `reasoning` FAILs.

## Capabilities

| Capability | Meaning | Checked where |
|---|---|---|
| `reasoning` | General analysis / design quality | `reasoning` profile's `required_capabilities` |
| `structured_output` | Can return JSON matching a schema | Required for **every** agent call (all agents use structured output) and for the doctor probe |
| `coding` | Code generation and review | `coding` profile's `required_capabilities` — every `coding` candidate, including fallbacks, must declare it |
| `fast` | Lower latency | Recognised name; no shipped profile uses it |
| `tool_calling` | Native tool calls | Declared only; not used (agents return file changes as structured output) |
| `vision`, `long_context`, `streaming` | — | Recognised names; not used yet |

A model is a **candidate** for a request only if its provider is enabled and it
declares every capability in the profile's `required_capabilities` **plus**
any capability the call needs (`structured_output` for agent calls). If no
model qualifies, the call fails immediately with
`No enabled model for profile '<name>' supports [...]`
(kind `unsupported_capability`).

## How a request is routed

For a request on profile `P`:

1. **Candidates** — `preferred_model`, then `fallback_models` in order
   (duplicates removed), filtered to capability-compatible models on enabled
   providers. Fallbacks are never capability-incompatible.
2. **Temperature** — the profile's `temperature` is applied.
3. **Try each candidate in order:**
   1. Call the model. On a **retryable** error, wait
      `retry_backoff_seconds × attempt` and retry the *same* model, up to
      `limits.max_agent_retries` extra attempts.
   2. For structured calls, **validate** the response against the agent's
      schema (see below), with bounded repair attempts.
   3. On success, return. The response records every attempt (provider,
      model, attempt number, status, error kind, latency, tokens) for the
      project's execution history.
   4. On failure, if the error kind is **fallback-eligible**, move to the next
      candidate; otherwise stop immediately.
   5. If the error was `provider_unavailable` or `network`, the provider is
      marked down for this request: remaining candidates **on the same
      provider are skipped** (another model on an unreachable server cannot
      help). Candidates on other providers are still tried.
4. If every candidate fails, the last error is raised with the full attempt
   list; the workflow stage moves to `FAILED`.

### Error kinds

Providers translate their failures into factory-level kinds
(`src/software_factory/llm/errors.py`), so retry and fallback policy lives in
one place.

| Kind | Retried on same model | Falls back to next model | Skips same-provider fallbacks |
|---|---|---|---|
| `timeout` | yes | yes | no |
| `rate_limit` | yes | yes | no |
| `provider_unavailable` | yes | yes | **yes** |
| `network` | yes | yes | **yes** |
| `malformed_response` | yes | yes | no |
| `model_unavailable` | no | yes | no |
| `authentication` | no | yes | no |
| `context_length_exceeded` | no | yes | no |
| `invalid_structured_output` | no (repaired instead) | yes | no |
| `unsupported_capability` | no | no (raised before any call) | — |
| `tool_call_failure`, `configuration` | no | no | — |

How the Ollama adapter maps responses (the OpenAI-compatible adapter's mapping
is [below](#error-mapping)):

| Ollama response | Kind |
|---|---|
| Connection refused | `provider_unavailable` — `Cannot reach Ollama at <base_url>` |
| Request exceeds `timeout_seconds` | `timeout` — `Request timed out after <N>s` |
| HTTP 404 or "model … not found" | `model_unavailable` — `Model '<m>' is not available: …` |
| HTTP 401/403 | `authentication` |
| HTTP 429 | `rate_limit` |
| Error text mentions context length/window | `context_length_exceeded` |
| Other HTTP 5xx | `provider_unavailable` |
| Other HTTP 4xx, non-JSON body, missing message content | `malformed_response` |
| Other transport errors | `network` |

### Structured output

Every agent call asks for a Pydantic schema. The router:

1. appends an instruction to answer with a single JSON object matching the
   JSON schema, and passes the schema to the provider — Ollama gets it as
   `format` (schema-constrained output); an OpenAI-compatible provider sends
   it according to its `structured_output` option (`json_schema`,
   `json_object` or `prompt`);
2. extracts the JSON object from the reply (tolerating a ```` ```json ```` fence
   or surrounding prose);
3. validates it against the schema;
4. if invalid, sends the reply and the validation errors back to the same
   model and asks for corrected JSON — up to
   `limits.max_structured_output_repairs` times;
5. if still invalid, raises `invalid_structured_output`
   (`Output did not match <Schema>: …`), which falls back to the next
   candidate.

After schema validation, each agent also runs deterministic checks of its own
(duplicate ids, dependency cycles, …); those failures fail the stage without
model fallback.

### Worked example (shipped config)

`reasoning` → `[local_model (qwen2.5:14b)]`; `coding` → `[local_coder
(qwen2.5-coder:14b), local_model (qwen2.5:14b)]`.

- Reasoning: 14B times out three times (1 + 2 retries) → no fallback → stage
  `FAILED`; history shows the 3 failed attempts. 14B not pulled →
  `model_unavailable` (no retry) → stage `FAILED`.
- Coding: coder model times out three times → fallback to `qwen2.5:14b`.
  Coder model not pulled → quick `model_unavailable` → `qwen2.5:14b` answers
  (every Coding/Testing/Code Review call pays that failed attempt; pull the
  coder model).
- Ollama not running → `provider_unavailable` after retries → the fallback on
  the same provider is skipped → stage `FAILED` with `Cannot reach Ollama at …`.

### Worked example (with fallbacks, optional)

If you add a fallback, e.g. `reasoning` → `[local_model (qwen2.5:14b),
local_small (qwen2.5:7b)]`:

- 14B times out three times → fallback to 7B → 7B answers: stage succeeds;
  history shows 3 failed + 1 successful attempt.
- 14B not pulled → `model_unavailable` (no retry) → 7B answers.
- Ollama not running → `provider_unavailable` on 14B after retries → 7B is
  skipped (same provider) → stage `FAILED`.

The shipped `coding` profile is itself an example of this pattern (see
above).

## Common changes

### Use different local models

Change only the `model:` values (and `capabilities` if they differ), pull the
models, and probe:

```yaml
models:
  local_model:
    provider: ollama
    model: qwen2.5:32b
    capabilities: [reasoning, coding, structured_output]
    options:
      num_ctx: 32768
```

Keep `coding` in `capabilities` while the model serves the `coding` profile
(as a preferred model or a fallback); without it the router never selects it
for that profile.

```bash
ollama pull qwen2.5:32b
```

```bash
uv run factory doctor --probe
```

### Use a single model (low-memory machines)

The shipped config uses two 14B models (one loaded at a time with
`OLLAMA_MAX_LOADED_MODELS=1`, ~15 GB at 32k context). On a machine with less
memory, use one smaller model for both profiles and shrink the context:

```yaml
models:
  local_model:
    provider: ollama
    model: qwen2.5:7b
    capabilities: [reasoning, coding, structured_output]
    options:
      num_ctx: 16384

profiles:
  reasoning:
    preferred_model: local_model
    required_capabilities: [reasoning]
    temperature: 0.1
  coding:
    preferred_model: local_model
    required_capabilities: [coding]
    temperature: 0.2

limits:
  max_context_chars: 20000     # less code per call to fit the smaller context
```

Both `reasoning` and `coding` profiles must exist (doctor FAILs a missing
one), and every model key a profile references must exist, so remove unused
models and profiles together. Missing models are only WARNs; a profile with no
installed model is a FAIL.

### Add a fallback or a per-role model

Optional. Add more entries under `models` and reference them from a profile.
Every candidate must declare the profile's `required_capabilities` (plus
`structured_output`):

```yaml
models:
  local_model:
    # ... as shipped (qwen2.5:14b)
  local_coder:
    # ... as shipped (qwen2.5-coder:14b)
  local_small:
    provider: ollama
    model: qwen2.5:7b
    capabilities: [reasoning, structured_output]
    options:
      num_ctx: 16384

profiles:
  reasoning:
    preferred_model: local_model
    fallback_models: [local_small]
    required_capabilities: [reasoning]
    temperature: 0.1
  coding:
    preferred_model: local_coder
    fallback_models: [local_model]
    required_capabilities: [coding]
    temperature: 0.2
```

Pull the extra model (`ollama pull qwen2.5:7b`)
and run `uv run factory doctor --probe`.

### Disable thinking or keep a model loaded

For models that support a thinking mode, and to control how long Ollama keeps
the model in memory:

```yaml
    options:
      num_ctx: 16384
      think: false        # sent as a top-level request field
      keep_alive: 30m     # sent as a top-level request field
```

## OpenAI-compatible servers and cloud APIs

Provider type `openai_compatible` (`src/software_factory/llm/providers/openai_compatible.py`)
talks to any server that implements the Chat Completions format:

- **local:** LM Studio, llama.cpp server, vLLM, Ollama's own `/v1` endpoint;
- **cloud:** DeepSeek, OpenAI, Groq, OpenRouter, and others.

Switching the factory to one of them is a **config-only change** — no agent,
workflow or state code changes. `config/factory.yaml` ships commented
`lmstudio` and `deepseek` examples under `providers`.

### Provider settings

| Key | Required | Meaning |
|---|---|---|
| `type` | yes | `openai_compatible` |
| `base_url` | **yes** | The part before `/chat/completions`, e.g. `http://localhost:1234/v1` or `https://api.deepseek.com/v1`. Missing → `Provider '<name>' needs a base_url (e.g. https://api.openai.com/v1)`. |
| `api_key_env` | no | Name of the environment variable holding the API key. Omit for local servers without auth. Read at call time and sent as `Authorization: Bearer …`; if the variable is unset the call fails with `Provider '<name>' requires environment variable <VAR>`. |
| `timeout_seconds` | no (300) | Request timeout. |
| `options.structured_output` | no | How a JSON answer is requested: `json_schema` (default — sends `response_format` with the JSON schema), `json_object` (sends `response_format: {type: json_object}`, for servers without schema support such as DeepSeek), or `prompt` (sends nothing extra; relies on the instructions). Any other value is a configuration error. The router validates the answer against the schema in every mode. |

Other keys under the **provider's** `options` are not sent anywhere. Request
parameters belong in the **model's** `options`, which are merged into the
request body (e.g. `max_tokens`, `top_p`).

`factory doctor` checks these providers too: `Provider: <name>` calls
`GET {base_url}/models` (PASS with `N model(s) available`; FAIL with
`authentication failed — check the API key`, `Cannot reach <base_url>`, or
the missing-variable message), and `Model: <key>` checks that the model's
name appears in that list. `factory doctor --probe` sends the same tiny
structured request as for Ollama.

### Example: local LM Studio (no key)

Start LM Studio's local server (default port 1234) with a model loaded, then:

```yaml
providers:
  lmstudio:
    type: openai_compatible
    base_url: http://localhost:1234/v1
    timeout_seconds: 600

models:
  local_model:
    provider: lmstudio
    model: <model id shown by LM Studio / GET http://localhost:1234/v1/models>
    capabilities: [reasoning, coding, structured_output]
    context_window: 32768

profiles:
  reasoning:
    preferred_model: local_model
    required_capabilities: [reasoning]
    temperature: 0.1
  coding:
    preferred_model: local_model
    required_capabilities: [coding]
    temperature: 0.1
```

The context length is set when the model is loaded in LM Studio, not through
`num_ctx`. The same shape works for llama.cpp server or vLLM (their own
`base_url`), and for Ollama's OpenAI-compatible endpoint
`http://localhost:11434/v1` — verified live on 2026-09-29 with `qwen2.5:14b`
(structured answer correct).

### Example: DeepSeek (cloud)

```yaml
providers:
  deepseek:
    type: openai_compatible
    base_url: https://api.deepseek.com/v1
    api_key_env: DEEPSEEK_API_KEY
    options:
      structured_output: json_object   # DeepSeek accepts json_object, not json_schema

models:
  cloud_model:
    provider: deepseek
    model: deepseek-chat               # check the provider's current model names
    capabilities: [reasoning, coding, structured_output]
    options:
      max_tokens: 8192

profiles:
  reasoning:
    preferred_model: cloud_model
    required_capabilities: [reasoning]
    temperature: 0.1
  coding:
    preferred_model: cloud_model
    required_capabilities: [coding]
    temperature: 0.1
```

```bash
export DEEPSEEK_API_KEY=...   # or put it in .env
```

```bash
uv run factory doctor --probe
```

OpenAI, Groq and OpenRouter follow the same pattern with their own
`base_url`, key variable and model names; keep the default
`structured_output: json_schema` where the API supports JSON-schema responses.

### Example: mixing — reasoning in the cloud, coding local

Providers and models can be mixed freely; each profile picks its own:

```yaml
providers:
  ollama:
    type: ollama
    base_url: http://localhost:11434
    timeout_seconds: 600
  deepseek:
    type: openai_compatible
    base_url: https://api.deepseek.com/v1
    api_key_env: DEEPSEEK_API_KEY
    options:
      structured_output: json_object

models:
  local_model:
    provider: ollama
    model: qwen2.5:14b
    capabilities: [reasoning, coding, structured_output]
    context_window: 32768
    options:
      num_ctx: 32768
  cloud_reasoning:
    provider: deepseek
    model: deepseek-chat
    capabilities: [reasoning, structured_output]

profiles:
  reasoning:                     # design phase on the cloud model…
    preferred_model: cloud_reasoning
    fallback_models: [local_model]   # …falling back to the local model
    required_capabilities: [reasoning]
    temperature: 0.1
  coding:                        # build phase stays local
    preferred_model: local_model
    required_capabilities: [coding]
    temperature: 0.1
```

If DeepSeek is unreachable or rejects the key, `reasoning` calls fall back to
`local_model`: `provider_unavailable`/`network` only skip fallbacks on the
**same** provider, and `authentication` is fallback-eligible. A missing
`DEEPSEEK_API_KEY` variable is different — it is a configuration error, not an
`LLMError`, so the stage fails with the hint instead of falling back.

Note: with a cloud provider, requirements, artifacts and (for `coding`) source
code are sent to that third party.

### Error mapping

| Server response | Kind |
|---|---|
| Connection failure | `provider_unavailable` — `Cannot reach <base_url>` |
| Request exceeds `timeout_seconds` | `timeout` — `Request timed out after <N>s` |
| HTTP 401/403 | `authentication` — hint names the `api_key_env` variable |
| HTTP 404, or error mentions `model_not_found` / "does not exist" | `model_unavailable` — `Model '<m>' is not available: …` |
| HTTP 429 | `rate_limit` |
| Error mentions context length/window | `context_length_exceeded` |
| Other HTTP 5xx | `provider_unavailable` |
| Other HTTP 4xx, non-JSON body, missing `choices[0].message.content` | `malformed_response` |
| Other transport errors | `network` |

### API keys

API keys are never stored in config: the provider names an environment
variable (`api_key_env`), which is read only when that provider is used and
never written to config, state, artifacts, history or logs. Set the variable in
your shell or in `.env` in the directory you run the CLI from (loaded at
startup; shell values win) — see
[configuration](../setup/configuration.md#the-env-file).

Tests: `tests/unit/test_openai_compatible_provider.py` (request shape, the
three structured-output modes, error mapping, health and metadata) and
`tests/integration/test_provider_switching.py`, whose
`test_same_workflow_runs_on_an_openai_compatible_api` runs the same workflow
against a mocked OpenAI-compatible API and checks the key is never persisted.

## Related documents

- [Local model setup](local_model_setup.md)
- [Model selection](model_selection.md)
- [Configuration reference](../setup/configuration.md)
- [Troubleshooting](../setup/troubleshooting.md)
- [LLM architecture](../architecture/llm_architecture.md)
