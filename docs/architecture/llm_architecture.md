# LLM Architecture

How agents talk to models without knowing which provider or model is used.
Configuration details: [model configuration](../models/model_configuration.md),
[model selection](../models/model_selection.md),
[local model setup](../models/local_model_setup.md).

## Two boundaries

`llm/interfaces.py` defines two protocols:

| Protocol | Who depends on it | Who implements it | Methods |
|---|---|---|---|
| `LLMClient` | Agents (and `doctor --probe`) | `ModelRouter` | `generate(request)`, `generate_structured(request, schema)`, `supports(capability, profile=None)` |
| `ModelProvider` | `ModelRouter` via `ModelRegistry` | `OllamaProvider`, `OpenAICompatibleProvider` (and the test-only `scripted` provider) | `generate(request, model, options)`, `health_check()`, `get_model_metadata(model)` |

```text
Agent (LLMAgent.run)
  │  LLMRequest(model_profile="reasoning", messages, metadata)
  ▼
LLMClient  ── implemented by ──►  ModelRouter
                                    │ profile → candidate models (capability-filtered)
                                    │ retries / backoff / fallback / structured validation
                                    ▼
                                  ModelRegistry ── provider_for(model_key)
                                    │
                                    ▼
                                  providers/base.create_provider(type)
                                    │  "ollama"            → OllamaProvider
                                    │  "openai_compatible" → OpenAICompatibleProvider
                                    ▼
          Ollama HTTP API (/api/chat, /api/tags, /api/show)
          or any Chat Completions server ({base_url}/chat/completions, {base_url}/models)
```

Nothing outside `llm/providers/` knows which provider is in use; the dependency
test forbids agents, orchestration and core from importing `llm.providers`
or `httpx` (see [system architecture](system_architecture.md#dependency-boundaries)).

## Normalized types (`llm/models.py`)

- `Capability`: `reasoning`, `coding`, `fast`, `structured_output`,
  `tool_calling`, `vision`, `long_context`, `streaming`.
- `LLMRequest`: `messages`, `model_profile` (default `reasoning`),
  `temperature`, `structured_output_schema` (JSON schema, set by
  `generate_structured`), `max_output_tokens`, `metadata`
  (`project_id`, `agent`).
- `LLMResponse`: `content`, `provider`, `model`, `model_profile`, `usage`
  (input/output tokens), `latency_ms`, `parsed` (validated schema instance),
  `calls` (one `ProviderCall` per attempt).
- `ProviderCall`: `provider`, `model`, `attempt`, `status`
  (`success` / `failed` / `invalid_output`), `latency_ms`, `error_kind`,
  `error`, `usage`. These become `llm_calls` in execution history.
- `ProviderHealth`, `ModelMetadata` (availability, parameter size,
  quantization, context length, size, family) — used by `factory doctor`.

Tool calling and streaming are declared capabilities only; no code path sends
tools or streams tokens yet. **Planned — not implemented yet.**

## Errors (`llm/errors.py`)

Providers map their failures onto `LLMErrorKind`, so retry/fallback policy is
written once in the router.

| Kind | Retry same model (`RETRYABLE_KINDS`) | Try fallback model (`FALLBACK_KINDS`) |
|---|---|---|
| `timeout` | yes | yes |
| `rate_limit` | yes | yes |
| `provider_unavailable` | yes | yes |
| `network` | yes | yes |
| `malformed_response` | yes | yes |
| `model_unavailable` | no | yes |
| `authentication` | no | yes |
| `context_length_exceeded` | no | yes |
| `invalid_structured_output` | no (handled by repair loop) | yes |
| `tool_call_failure` | no | no |
| `unsupported_capability` | no | no |
| `configuration` | no | no |

`LLMError` is a `FactoryError` and carries `kind`, `provider`, `model`, `hint`
and `calls` (every attempt made before it became final).

## Model registry (`llm/registry.py`)

Reads `providers`, `models` and `profiles` from config
([configuration](../setup/configuration.md)):

- `candidates(profile, required)` returns
  `[preferred_model, *fallback_models]` (deduplicated, in order), keeping only
  models whose provider is enabled and whose declared `capabilities` include
  every required capability (the call's required set ∪ the profile's
  `required_capabilities`). Fallbacks are therefore always
  capability-compatible.
- Providers are created lazily on first use, so a disabled or unused
  provider never needs its API key. `provider_for` raises
  `ConfigurationError` for a disabled provider.

## Model router (`llm/router.py`)

`ModelRouter` implements `LLMClient`.

```text
_run(request, required, finish_schema)
  candidates = registry.candidates(profile, required)
  none? → LLMError(unsupported_capability) with hint
  temperature unset? → profile.temperature
  down_providers = {}
  for model in candidates:
      provider of model in down_providers? → skip
      response = _call_with_retries(model)          # attempts = 1 + limits.max_agent_retries
      if structured: response = _validate_structured(...)
      return response (with calls)
    on LLMError:
      kind not in FALLBACK_KINDS → stop
      kind in {provider_unavailable, network} → add provider to down_providers
      → next candidate
  raise final LLMError carrying all calls
```

- **Retries:** only `RETRYABLE_KINDS`; sleep
  `limits.retry_backoff_seconds × attempt` between attempts (linear backoff).
- **Fallback:** after retries for one model are exhausted with a
  `FALLBACK_KINDS` error, the next capability-compatible candidate is tried.
- **Unreachable provider short-circuit:** if a model's final error is
  `provider_unavailable` or `network`, its provider is marked down for the
  rest of that call and every remaining fallback model on the **same
  provider** is skipped (an unreachable server cannot serve another model).
  Fallback still proceeds to candidates on other providers. Other
  fallback-eligible errors (`model_unavailable`, `authentication`,
  `context_length_exceeded`, `invalid_structured_output`, and the other
  retryable kinds) still fall back to same-provider models.
- **Structured output** (`generate_structured`):
  1. Requires capability `structured_output`.
  2. Puts `schema.model_json_schema()` in `structured_output_schema` (the
     provider sends it natively — Ollama `format`, OpenAI-compatible
     `response_format` per its `structured_output` option) **and** appends a user
     message with the schema and "JSON only".
  3. Extracts JSON (strips a ```` ```json ```` fence, or takes the outer
     `{…}`), validates with Pydantic.
  4. On failure, marks the attempt `invalid_output` and sends a repair turn
     (previous answer + validation errors, up to 8), at most
     `limits.max_structured_output_repairs` times. Then raises
     `invalid_structured_output`, which may fall back to another model.
- `supports(capability, profile)` is true when any (or the given) profile has
  a candidate with that capability.
- A `ConfigurationError` raised while creating a provider (for example an
  unknown provider type) is not an `LLMError`; it propagates to the agent
  node and the stage becomes `FAILED` with the hint.

## Provider adapters

### Provider type registry (`llm/providers/base.py`)

Config names a provider `type`; `create_provider` maps it to a factory.
Two types are built in and registered lazily on first lookup: `ollama`
(native Ollama API) and `openai_compatible` (any server speaking the Chat
Completions format). An unknown type still raises `ConfigurationError`
(`Provider '<name>' has type '<type>', which is not implemented`) listing the
implemented types. Tests register an extra type `scripted` to prove provider
switching needs no agent or workflow change
(`tests/integration/test_provider_switching.py`).

`read_api_key(provider, config)` reads `api_key_env` from the environment at
call time and raises `ConfigurationError`
(`Provider '<name>' requires environment variable <VAR>`) if it is unset.
Returns `None` when no `api_key_env` is configured. The key is never stored in
config, state, artifacts, history or logs. The OpenAI-compatible adapter uses
it; the Ollama adapter does not.

Adding another provider still means implementing `ModelProvider` and calling
`register_provider_type` — no agent, workflow or state change.

### Ollama (`llm/providers/ollama.py`)

| Operation | Endpoint | Notes |
|---|---|---|
| `generate` | `POST /api/chat` (`stream: false`) | `format` = JSON schema when structured; model `options` go into `options` except `think` and `keep_alive`, which are top-level; `temperature` and `num_predict` (from `max_output_tokens`) added to `options`; tokens from `prompt_eval_count` / `eval_count` |
| `health_check` | `GET /api/tags` (5 s timeout) | Healthy + installed model names, or an actionable detail (`ollama serve`) |
| `get_model_metadata` | `/api/tags` then `POST /api/show` | Matches `name` or `name:latest`; reads `*.context_length` from `model_info` |

Error normalization:

| Condition | Kind |
|---|---|
| `httpx.TimeoutException` | `timeout` (hint: raise `timeout_seconds`) |
| `httpx.ConnectError` | `provider_unavailable` (hint: `ollama serve`) |
| other `httpx.HTTPError` | `network` |
| HTTP 404 or "model … not found" | `model_unavailable` (hint: `ollama pull <model>`) |
| HTTP 401/403 | `authentication` |
| HTTP 429 | `rate_limit` |
| body mentions context length/window | `context_length_exceeded` |
| HTTP ≥ 500 | `provider_unavailable` |
| other 4xx, non-JSON body, missing `message.content` | `malformed_response` |

### OpenAI-compatible (`llm/providers/openai_compatible.py`)

One adapter for every server that speaks the Chat Completions format: local
engines (LM Studio, llama.cpp server, vLLM, Ollama's `/v1` endpoint) and cloud
APIs (DeepSeek, OpenAI, Groq, OpenRouter, …). `LLMRequest`/`LLMResponse` stay
the domain contract; the adapter only translates.

Construction fails with `ConfigurationError` when `base_url` is missing or
`options.structured_output` is not one of `json_schema`, `json_object`,
`prompt`. `base_url` is the part before `/chat/completions`
(e.g. `https://api.deepseek.com/v1`); a trailing `/` is stripped.

| Operation | Endpoint | Notes |
|---|---|---|
| `generate` | `POST {base_url}/chat/completions` | Body: `model`, `messages`, then the **model's** `options` merged in (e.g. `max_tokens`, `top_p`); `temperature` and `max_tokens` (from `max_output_tokens`) set when given. Structured requests add `response_format` per `structured_output` (below). Content from `choices[0].message.content`; tokens from `usage.prompt_tokens` / `usage.completion_tokens`. |
| `health_check` | `GET {base_url}/models` (10 s timeout) | Healthy + `N model(s) available` with the listed ids; 401/403 → `authentication failed — check the API key`; other status → `HTTP <status> from <base_url>/models`; connect failure → `Cannot reach <base_url>` |
| `get_model_metadata` | `GET {base_url}/models` | Available if an entry's `id` equals the model name; `context_length` from the entry's `context_length` or `max_context_length` if present (integer); `family` from `owned_by` |

Headers: `Authorization: Bearer <key>` only when `api_key_env` is set; the key
is read via `read_api_key` for every client created (call time), so a missing
variable raises `ConfigurationError` naming it.

`options.structured_output` (provider option; the only provider option the
adapter reads — other provider `options` are **not** sent):

| Value | What is sent when a schema is requested |
|---|---|
| `json_schema` (default) | `response_format: {type: json_schema, json_schema: {name: factory_output, schema: …}}` |
| `json_object` | `response_format: {type: json_object}` — for servers without schema support (e.g. DeepSeek) |
| `prompt` | nothing extra; relies on the router's "JSON only" instruction |

In every mode the router still validates the answer against the schema and
runs its repair loop.

Error normalization:

| Condition | Kind |
|---|---|
| `httpx.TimeoutException` | `timeout` (hint: raise `timeout_seconds` or use a smaller model) |
| `httpx.ConnectError` | `provider_unavailable` (hint: check `base_url` / server running) |
| other `httpx.HTTPError` | `network` |
| HTTP 401/403 | `authentication` (hint names the `api_key_env` variable) |
| HTTP 404, or error message/code containing `model_not_found` or "does not exist" | `model_unavailable` |
| HTTP 429 | `rate_limit` |
| error mentions "context" with "length" or "window" | `context_length_exceeded` |
| HTTP ≥ 500 | `provider_unavailable` |
| other 4xx, non-JSON / non-object body, missing or non-text `choices[0].message.content` | `malformed_response` |

The error text is taken from the body's `error.message` (and `error.code` or
`error.type`) when present. Verified live on 2026-09-29 against Ollama's
OpenAI-compatible endpoint (`http://localhost:11434/v1`, `qwen2.5:14b`):
the structured answer was correct. Tests: `tests/unit/test_openai_compatible_provider.py`
and `tests/integration/test_provider_switching.py::test_same_workflow_runs_on_an_openai_compatible_api`
(which also checks the key is never persisted).

## Agents and prompts

Agents (`agents/base.py`, `LLMAgent`) build an `LLMRequest` from two
versioned prompt files (`prompts/<agent>/system.md`, `task.md`), call
`generate_structured` with their output schema, then run deterministic
`validate_output` checks. Prompt versions are recorded as
`<front-matter version>+<sha256[:8] of body>`.

| Profile | Agents | Default config |
|---|---|---|
| `reasoning` | Requirement, Architecture, Technology Selection, Implementation Planning, Impact Analysis ([requirement](../agents/requirement_agent.md), [architecture](../agents/architecture_agent.md), [technology selection](../agents/technology_selection_agent.md), [implementation planning](../agents/implementation_planning_agent.md)) | `local_model` (`qwen2.5:14b`), no fallback; requires `reasoning` |
| `coding` | Coding, Testing, Code Review ([coding](../agents/coding_agent.md), [testing](../agents/testing_agent.md), [code review](../agents/code_review_agent.md)) | `local_coder` (`qwen2.5-coder:14b`), fallback `local_model` (`qwen2.5:14b`), temperature 0.2; requires `coding` |

The shipped config (user decision 2026-09-30) uses two models: a general model
for reasoning and a code-specialised one for coding, with the general model as
coding fallback. Ollama should run with `OLLAMA_MAX_LOADED_MODELS=1` so only one
is in memory. See [model configuration](../models/model_configuration.md).

`LLMAgent.run` adds one layer above the router: when the agent's deterministic
`validate_output` raises `AgentOutputError`, the model gets
`validation_repairs` (1) correction turn with the exact reason ("Your answer
failed the factory's checks: …"); a second failure propagates.

The build agents' prompts carry source code, so their models are configured
with a 32k context window, and `limits.max_context_chars` (default 40 000)
caps how much source code one call receives. `factory doctor` checks both
the `reasoning` and `coding` profiles, for Ollama and OpenAI-compatible
providers alike (via each adapter's `health_check` / `get_model_metadata`).
`factory evaluate` runs the real agents on five fixed cases to check that the
configured model is good enough (see
[model selection](../models/model_selection.md#verify-with-factory-evaluate)).

## What gets recorded

Every agent execution record contains `provider`, `model`, `model_profile`,
`prompt_versions`, `agent_version` and `llm_calls` — one entry per attempt,
retry, repair and fallback, with status, latency, tokens and error kind.
Failed runs recover the attempts from `LLMError.calls`. See
[persistence](persistence_architecture.md#execution-history).
