# Troubleshooting

Start every investigation with the health check; it usually names the problem
and the fix:

```bash
uv run factory doctor
```

```bash
uv run factory doctor --probe
```

To check whether the configured model is good enough for the factory's tasks
(not just reachable), run `uv run factory evaluate` — see
[`factory evaluate` reports FAIL](#factory-evaluate-reports-fail).

## How errors are reported

- CLI errors print as `Error: <message>` followed by `Hint: <what to do>` and
  exit with code 1.
- Model errors are prefixed with their normalized kind and the provider/model,
  e.g. `[provider_unavailable] ollama/qwen2.5:14b: Cannot reach Ollama at ...`.
- When a workflow stage fails, the project moves to `FAILED`; `factory status`
  shows `Failed in <stage>: <message>` plus a hint, and the details go to
  `errors.md` in the project folder and to `factory history`.
- The log file is `<workspace>/logs/factory.log` (JSON lines, secrets
  redacted). Nothing is logged to the terminal.

Where to look for a project:

```bash
uv run factory status <project>
```

```bash
uv run factory history <project>
```

```bash
uv run factory history <project> --json --limit 100
```

`history` lists every agent run with each provider attempt (model, attempt
number, status, error kind), so you can see which model failed, how often it
was retried and whether a fallback answered.

---

## Configuration

### `No factory configuration found`

The CLI found neither `--config`, `$FACTORY_CONFIG` (shell or `./.env`), nor
`./config/factory.yaml` in the current directory. As the hint says: run from
the repository root (which has `config/factory.yaml`), pass `--config` (the
option goes before the command), or set `FACTORY_CONFIG`:

```bash
uv run factory -c /path/to/software-factory/config/factory.yaml status my-project
```

### `Configuration file not found: <path>`

`--config` or `$FACTORY_CONFIG` points at a path that does not exist. Check
for typos and unset a stale variable:

```bash
unset FACTORY_CONFIG
```

### `Configuration file is not valid YAML: ...`

Fix the YAML syntax (indentation, unclosed brackets). The message includes the
line and column.

### `Invalid factory configuration: ...`

A value failed validation or a reference is broken. Common cases:

- `model '<key>' references unknown provider '<name>'` — the model's
  `provider` is not a key under `providers`.
- `profile '<name>' references unknown model '<key>'` — a `preferred_model` or
  `fallback_models` entry is not a key under `models`.
- A limit outside its allowed range (see
  [configuration](configuration.md#limits)).

### Doctor: `Configuration FAIL — profile 'reasoning' is missing`

The design agents use the `reasoning` profile. Define it under `profiles`.

### Doctor: `Prompts FAIL — missing: requirement/system.md, ...`

`factory.prompts_dir` does not point at the repository's `prompts/` folder.
Remember relative paths resolve against the config file's directory (the
shipped value is `../prompts`). At run time the same problem appears as
`Prompt file not found: <path>`.

### `Provider '<name>' has type '<type>', which is not implemented`

The implemented provider types are `ollama` and `openai_compatible` (the hint
lists them). Use `openai_compatible` for LM Studio, llama.cpp server, vLLM,
Ollama's `/v1` endpoint and cloud APIs such as DeepSeek, OpenAI, Groq or
OpenRouter — see
[model configuration](../models/model_configuration.md#openai-compatible-servers-and-cloud-apis).
Otherwise fix the typo, or set `enabled: false` on the provider and make sure
no profile depends on it.

### `Provider '<name>' needs a base_url (e.g. https://api.openai.com/v1)`

An `openai_compatible` provider has no `base_url`. Set it to the part before
`/chat/completions`, e.g. `http://localhost:1234/v1` (LM Studio),
`http://localhost:11434/v1` (Ollama) or `https://api.deepseek.com/v1`.

### `providers.<name>.options.structured_output must be one of ('json_schema', 'json_object', 'prompt')`

Fix the value. Use `json_schema` (default) where the server supports JSON-schema
responses, `json_object` where it only supports "any JSON object" (e.g.
DeepSeek), `prompt` where it supports neither.

### `Provider '<name>' requires environment variable <VAR>`

Raised when an `openai_compatible` provider with `api_key_env` is used (a
request, `factory doctor`, `factory evaluate`) and the variable is unset or
empty. Doctor shows it as `Provider: <name> FAIL`; at run time the stage fails
with this message (it is a configuration error, so no fallback model is
tried). Export it in your shell or add `VAR=value` to `.env` in the directory
you run the CLI from (a value already set in the shell wins over `.env`).
Local servers without auth need no `api_key_env` at all.

### A value in `.env` is ignored

- `.env` is read from the **current working directory**, not from the config
  file's folder — run the CLI from the repository root.
- A variable already exported in your shell (even to an empty-looking value)
  takes precedence; `unset` it or open a new shell.
- Lines with an empty value are skipped.

### Doctor: `Dependencies FAIL — missing: ...`

```bash
uv sync
```

### Doctor: `Workspace FAIL` / `Database FAIL`

The workspace directory could not be created or written. Check
`factory.workspace`, its permissions, and free disk space.

### Doctor: `Git FAIL — git not found`

Install git and make sure it is on `PATH`. On macOS either of:

```bash
xcode-select --install
```

```bash
brew install git
```

---

## Model server and models

### `Cannot reach Ollama at http://localhost:11434`

Appears in doctor as `Provider: ollama FAIL` (every `Model:` row is then
SKIPPED with `provider unreachable`, and `--probe` does not run), and at run
time as `[provider_unavailable] ...` after the configured retries. The router
does not try the other fallback models on the same (unreachable) provider, so
with the shipped all-Ollama config the stage fails right after the preferred
model's retries.

1. Is Ollama installed?

   ```bash
   ollama --version
   ```

2. Is the server running? Start it:

   ```bash
   ollama serve
   ```

   (or open the Ollama app / `brew services start ollama`).
3. Is `providers.ollama.base_url` right? Test it directly:

   ```bash
   curl http://localhost:11434/api/tags
   ```

See [local model setup](../models/local_model_setup.md).

### Doctor: `Provider: <name> FAIL — Cannot reach <base_url>` (OpenAI-compatible)

The `openai_compatible` provider's server did not answer
`GET {base_url}/models`. For a local server (LM Studio, llama.cpp, vLLM),
start it and check the port; for a cloud API, check the URL and your network.
Test it directly:

```bash
curl http://localhost:1234/v1/models
```

At run time the same problem is `[provider_unavailable] ...: Cannot reach <base_url>`.

### Doctor: `Provider: <name> FAIL — authentication failed — check the API key`

The server answered `/models` with 401/403. The variable named by
`api_key_env` holds a wrong, expired or revoked key (or one for another
provider). At run time this is kind `authentication`
(`HTTP 401: …`, hint `Check the API key in <VAR>.`); it is not retried, but a
fallback model on another provider is tried. Other statuses show as
`HTTP <status> from <base_url>/models`.

### Doctor: `Model: <key> WARN — <model> is not installed (...)`

A missing model is always a WARN. The note in brackets says what it is used
for, e.g. `(preferred for reasoning; preferred for coding)` for the shipped
`local_model`, `(fallback for coding)` or `(not used by any profile)`. Whether
you can work depends on the **Profile** rows below it, not on this row. With
the shipped single-model config, a missing `local_model` also makes both
profiles FAIL (see below). Pull the model:

```bash
ollama pull qwen2.5:14b
```

For an `openai_compatible` provider this means the model name is not among
the `id`s returned by `GET {base_url}/models` (the hint then says to check
the model name against the provider's model list): load the model in the
server (LM Studio) or correct `models.<key>.model` to the provider's exact
model name.

### Doctor: `Profile: <name> WARN — preferred model missing; will use fallback <model>`

Only appears if you have added `fallback_models` to a profile (the shipped
config has none). The agents using that profile will run on the fallback
model; pull the preferred model to use it again.

### Doctor: `Profile: <name> FAIL — no installed model`

None of the profile's models (preferred or fallbacks) is pulled, so the agents
using it cannot run. The hint lists the models to choose from; pull one:

```bash
ollama pull qwen2.5:14b
```

### Doctor: `Profile: <name> FAIL — not configured`

The `reasoning` or `coding` profile is missing from `profiles`. Both are
needed: `reasoning` for the design agents, `coding` for the Coding, Testing
and Code Review agents. On a single-model machine, point `coding` at your
reasoning model (it must declare the `coding` capability) — see
[model configuration](../models/model_configuration.md#use-a-single-model-low-memory-machines).

`factory doctor --probe` probes the `reasoning` and `coding` profiles; a probe
FAIL for a profile means its model cannot produce structured output.

### `Model '<model>' is not available: ...`

Run-time version of the above (Ollama returned 404 / "model not found"; an
OpenAI-compatible server returned 404, `model_not_found` or "does not exist"). The
router treats it as `model_unavailable` and moves on to the next fallback
model; if none succeeds the stage fails. Pull the model, then:

```bash
uv run factory retry <project>
```

### Doctor: `Model: <key> WARN — Model needs ~X GB; machine has Y GB RAM`

The model's size is more than 70% of your RAM. It may still load, but expect
swapping, very slow generation or timeouts. Choose a smaller model or
quantization — see [model selection](../models/model_selection.md).

### `No enabled model for profile '<name>' supports [...]`

Kind `unsupported_capability`. No model in the profile (preferred + fallbacks,
on enabled providers) declares all required capabilities. Structured-output
calls (all agent calls) additionally require `structured_output`. Add the
capability to the model's `capabilities` list if the model really has it, or
add a compatible fallback. Also raised when the model's provider is disabled.

### `Unknown model profile '<name>'`

A code path asked for a profile that is not configured. The hint lists the
configured profiles.

---

## Generation failures

### `Request timed out after <N>s`

Kind `timeout`. Retried per model (`limits.max_agent_retries`), then the next
fallback is tried (including fallbacks on the same provider — a timeout does
not mark the server as down). Local models on long prompts can be slow, especially the
first request while the model loads into memory. Options:

- Increase `providers.<name>.timeout_seconds` (shipped Ollama: 600; default 300).
- Use a smaller model or lower `num_ctx` in `models.<key>.options`.
- Close other memory-heavy applications.
- With two models configured (the shipped `qwen2.5:14b` + `qwen2.5-coder:14b`),
  start Ollama with `OLLAMA_MAX_LOADED_MODELS=1` so only one is in memory; two
  14B models at 32k context (~15 GB each) make a 32 GB machine swap. See
  [Ollama is slow or the machine swaps with two models](#ollama-is-slow-or-the-machine-swaps-with-two-models).

Then resume:

```bash
uv run factory retry <project>
```

### `Output did not match <Schema>: <field errors>` (invalid structured output)

Kind `invalid_structured_output`. The model's answer was not a JSON object
matching the agent's schema. Before this error is raised, the router already:

1. requested JSON with Ollama's `format` set to the schema (for an
   `openai_compatible` provider: `response_format` per its
   `structured_output` option),
2. extracted JSON from fenced/prose-wrapped output,
3. sent the validation errors back to the model up to
   `limits.max_structured_output_repairs` times,
4. tried each capability-compatible fallback model.

If it still fails: use a stronger model for the profile the failing agent uses
(`reasoning` for design agents, `coding` for Coding/Testing/Code Review), check
the probe with `factory doctor --probe`, or raise `max_structured_output_repairs`.
With an `openai_compatible` provider whose server rejects `response_format`
(an HTTP 400 surfaces as `malformed_response`), set
`options.structured_output: json_object` or `prompt`.
Then `factory retry <project>`.

### Doctor probe: `WARN — Model returned valid JSON but a wrong answer`

The model produced valid JSON but answered `2 + 3` incorrectly. It will likely
produce weak design artifacts; consider a stronger model.

### Agent validation errors

Even with valid JSON, agents run deterministic checks, e.g.
`Duplicate requirement ids: ...`, `Duplicate task ids: ...`,
`Task dependencies contain a cycle: ...`,
`Technology categories chosen more than once: ...`. The model first gets one
correction attempt with the exact reason ("Your answer failed the factory's
checks: …"; `LLMAgent.validation_repairs` = 1). If the corrected answer fails
too, the stage fails (no automatic fallback). Retry; if it recurs, use a
stronger model.

### Ollama is slow or the machine swaps with two models

The shipped config switches between `qwen2.5:14b` (reasoning) and
`qwen2.5-coder:14b` (coding). By default Ollama may keep both loaded. Measured:
one 14B model needs ~15 GB at 32k context (`num_ctx: 32768`), ~12 GB at 16k.
Restart the server so only one model is in memory:

```bash
OLLAMA_MAX_LOADED_MODELS=1 ollama serve
```

Switching models then costs a reload (a few seconds) instead of swapping. On
a smaller machine, lower `num_ctx` or use one model for both profiles — see
[model configuration](../models/model_configuration.md#use-a-single-model-low-memory-machines).

### `[rate_limit] ...` (cloud APIs)

HTTP 429 from the provider. Retried with backoff
(`limits.max_agent_retries`, `limits.retry_backoff_seconds`), then the next
fallback model is tried. Raise `retry_backoff_seconds`, add a local fallback
model, or check your plan's quota.

### `[context_length_exceeded] ...`

The prompt plus output exceeded the model's context. The router falls back to
the next compatible model. Raise `num_ctx` (up to the model's maximum, shown by
`factory doctor` as `ctx N`) if you have the memory, or use a model with a
longer context. In the build phase, lowering `limits.max_context_chars` (source
code sent per call, default 40000) also shrinks the prompt.

### `factory evaluate` reports FAIL

`factory evaluate` runs real agents on five fixed cases (details:
[model selection](../models/model_selection.md#verify-with-factory-evaluate)).
Any FAIL makes it exit with code 1; the full report is in
`<workspace>/evaluations/evaluation-<timestamp>.json`. Model output varies, so
first re-run just that case, e.g.:

```bash
uv run factory evaluate --case code_generation
```

| FAIL detail | Meaning / what to do |
|---|---|
| `error: …` | The agent call itself failed (timeout, unreachable provider, missing API key, invalid structured output after repairs, agent validation error). Fix it as for that message elsewhere on this page; `factory doctor --probe` usually shows the same problem faster. |
| `expected ≥3 functional requirements, got N` / `the one-second performance requirement was not captured as an NFR` | `requirement_extraction`: the `reasoning` model missed requirements in a clear request. Use a stronger reasoning model. |
| `did not ask any required clarification question for a vague request` | `ambiguity_detection`: the model would design from a vague request instead of asking. Use a stronger reasoning model. |
| `fewer than 2 components` / `requirements not mapped to a component: [...]` / `no alternative considered` | `architecture_reasoning`: weak architecture output. Use a stronger reasoning model. |
| `chose technology that is not installed or needs infrastructure: [...]` | `technology_fit`: the model ignored the environment (only python and git installed) or gave an empty rationale. Use a stronger reasoning model. |
| `returned no files` | `code_generation`: the Coding Agent returned no files or a change request. |
| `invalid file change: …` | A generated path was rejected by the filesystem tool (e.g. outside the folder). |
| `no tests were written (files: …)` | The generated code ran 0 tests — typically the tests were not written, or not in a `test*.py` file unittest discovers. |
| `generated tests failed (exit N): …` | The generated tests ran and failed (or errored); the last output line is shown. Use a stronger `coding` model. |
| `Unknown evaluation case(s): …` (an error, not a FAIL line) | A `--case` name is misspelled; the hint lists the valid names. |

The reference setup (`qwen2.5:14b`, M1 Pro 32 GB) passes all five. A model
that fails a case repeatedly is likely to struggle with the matching stage of
a real project.

---

## Project and workflow commands

### `Project '<id>' does not exist`

Use the project **id** (the slugified name), not the display name:

```bash
uv run factory list
```

### `Project '<id>' already exists`

Project ids come from the name (`"Employee Management"` → `employee-management`).
Pick a different name, or continue the existing project.

### `Invalid project id '<id>'`

Ids use lowercase letters, digits and `-`, max 63 characters, starting with a
letter or digit.

### `Project has no requirements yet`

```bash
uv run factory requirements <project> --file requirements.md
```

### `Requirements can only be replaced before the workflow starts`

Once started, give feedback by rejecting the requirements at the approval
gate: `factory reject <project> --reason "..."`.

### `Project '<id>' has already started`

Use `factory continue <project>` instead of `start`.

### `Project '<id>' is already running in another process`

Only one process may advance a project at a time (a lock file in
`projects/<id>/checkpoints/run.lock`). Wait for the other command to finish,
or pause it:

```bash
uv run factory pause <project>
```

The lock is released automatically when a process exits (even if it crashed),
so if no other factory process is running, simply rerun the command. To check:

```bash
ps aux | grep "factory"
```

### `Required questions are unanswered: Q1, Q3`

`factory continue` refuses to resume clarification until every **required**
question has an answer:

```bash
uv run factory questions <project>
```

```bash
uv run factory answer <project> Q1 "your answer"
```

### `Unknown question id '<id>'`

The hint lists the pending ids. Ids are case-insensitive (`q1` works).

### `Project is not waiting for clarification (status: ...)` / `Project is not waiting for an approval (status: ...)`

The command does not match the project's current state. `factory status`
shows what it is waiting for.

### `Project is waiting for '<stage>' approval, not '<other>'`

The `--stage` guard on `approve`/`reject` did not match. Omit `--stage` or use
the stage shown by `factory status`.

### `A rejection needs a reason so the agent can revise`

```bash
uv run factory reject <project> --reason "what should change"
```

### `Nothing to retry (status: ...)`

`retry` only applies to a `FAILED` stage or an interrupted run. Use
`factory continue` otherwise.

### `A hint can only be given when retrying a failed project`

`factory retry --hint "…"` is accepted only when the project is `FAILED`. To
guide an agent at an approval gate, use `factory reject --reason "…"`; for an
interrupted run, retry without `--hint`.

### `Only a failed project can be cancelled in this version`

`factory cancel` works only in `FAILED` state.

### The command was interrupted (Ctrl+C, crash, closed terminal)

Progress up to the last completed step is checkpointed. `factory status` shows
`Run was interrupted — factory continue resumes from the last checkpoint`:

```bash
uv run factory continue <project>
```

### Status shows `READY_FOR_DEVELOPMENT` and nothing continues

Normally the build phase starts right after the plan is approved. A project
that stopped here (for example one planned before the build phase existed)
continues with:

```bash
uv run factory continue <project>
```

---

## Build phase

Every project command's full output is saved under
`projects/<id>/artifacts/tool-runs/` (`NNN-<step>.log`); the latest run is
summarised in `test-results.md`, and the Testing Agent's analysis in
`test-analysis.md`. After fixing a cause by hand, `factory retry` resumes with
fresh budgets (fix attempts, review and security rounds). If the failure
happened while fixing a build/test problem, retry first re-runs the build and
tests, so your fix is actually checked. Once all tasks are done, any manual
edits in `source/` are committed automatically on retry
(`chore: manual changes before retry`).

You can also steer the agents instead of editing by hand:
`factory retry <project> --hint "what the real problem is"`. In the build phase
the hint is passed to the Testing and Coding agents as human guidance until the
failing work passes; the history records "Retry requested for stage … with
human hint: …".

### `The approved project commands need programs that are not available: X (not installed)`

The plan's commands start with a program that is not on your `PATH`. Nothing
has been written yet. The check uses your normal `PATH` **without** the
factory's own virtual environment (`security.isolate_project_environment`,
default `true`): `python` or `pytest` that exist only inside the factory's
`.venv` count as not installed, because the generated project must not depend
on them. Install it — for a Node/React plan, Node.js provides
`node`, `npm` and `npx`:

```bash
brew install node
```

```bash
uv run factory retry <project>
```

### `... need programs that are not available: X (not in security.allowed_commands)`

The program is installed but not allowed. If you trust it, add it to
`security.allowed_commands` in `config/factory.yaml`, then `factory retry`.
`sh`, `bash`, `zsh`, `sudo`, `su`, `rm`, `curl` and `wget` are always refused,
so a plan that relies on them has to change: reject the plan with a reason
(before development), or let a change request re-open it.

### `<step> still failing after N fix attempt(s): <command> exited <code>`

`build`, `lint`, `test` (or another step) kept failing after
`limits.max_debug_iterations` automatic fixes. Read `test-results.md` and the
log in `artifacts/tool-runs/`. Then either fix the code in `source/` by hand,
or fix the cause outside the code (missing tool, wrong command), and retry —
the fix budget restarts:

```bash
uv run factory retry <project>
```

Each round already tries up to `limits.fix_candidates` fixes against the real
commands. Local models may need several rounds for one failure; a
`--hint` on retry, raising `max_debug_iterations` or using a stronger `coding`
model (see
[model selection](../models/model_selection.md)) helps. A command that hits
`security.command_timeout_seconds` also counts as a failure — raise it for
slow installs or test suites.

### `No progress: the same failure came back 3 times in a row after automatic fixes`

Stuck detection: the build/test failure had the same signature (its error
lines, numbers ignored) three times in a row, so more automatic fixes would
only repeat themselves. Small local models get stuck like this on logic bugs
they cannot see (in the first live run: JSON object keys that come back as
strings after loading, ids reused after a delete). Read `test-results.md` and
`test-analysis.md`, decide what the real problem is, and retry with a hint:

```bash
uv run factory retry <project> --hint "JSON keys are strings after json.load; convert them back to int"
```

The hint goes to the Testing and Coding agents until the work passes. You can
also fix the code in `source/` by hand and retry without a hint.

### `None of the N proposed fixes could be applied` / `None of the N proposed fixes worked (...)`

A bug-fixing round ended without a fix to keep: candidates
(`limits.fix_candidates`) that could be applied failed the real checks and
were rolled back, and the last one could not be applied at all — usually
because an `edits` entry's `find` text was not in the file exactly once (see
the next entry). The first form means no candidate could be applied; the
second, e.g. `None of the 3 proposed fixes worked (2 were tested and still
failed, 1 could not be applied)`, gives the split. The files were rolled
back. After a failed edit the remaining candidates of the round must send
complete files; an answer that still uses `edits` is rejected (`` `edits` are
not allowed in this attempt … ``) and gets one correction attempt. Retry (the model's answers vary), optionally with a hint:

```bash
uv run factory retry <project> --hint "…"
```

### Edit for X: the `find` text occurs N times (must be exactly once)

The Coding Agent proposed a targeted edit whose `find` text is missing from
`X` (N = 0) or ambiguous (N > 1); `Edit for X: the file does not exist` is the
same problem for a missing file. In a multi-candidate fix round the candidate
is rolled back and the next one is asked; elsewhere the stage fails. Retry; if
it recurs, the model is copying text inexactly — a stronger `coding` model
helps.

### `Environment problem, not a code problem: ...`

The Testing Agent judged that the failure is caused by the machine (missing
program, service, network, permissions), not the code; the hint repeats its
suggested fix. Fix the environment, then `factory retry`: the build and tests
run again first, and only a new failure goes back to the Testing Agent.

Setup commands such as `npm install` and `pip install` need network access
(`security.network_enabled` is not enforced and does not block them).

### `<path> is not a git repository` / `<path> has uncommitted changes`

Existing-project mode (`--repository <path>`) needs a clean git repository;
the factory works on a branch `factory/<project-id>`. Initialise the
repository, or commit or stash your changes in it, then:

```bash
uv run factory retry <project>
```

`Could not switch to branch factory/<id>: ...` has the same fix (git's reason
is included).

### `<path> is not the root of its own git repository (git reports '...')`

A greenfield project's `source/` folder must be its own git repository. The
factory runs `git init` whenever `source/.git` is missing (the workspace may
lie inside another checkout, e.g. the factory's own repository) and then checks
`git rev-parse --show-toplevel`. This error means git still resolved a
different repository — for example `source/.git` exists but is not a valid
repository. Remove or repair `projects/<id>/source/.git` by hand, then
`factory retry`.

### `git commit failed: ...` / `Initial commit failed: ...` / `git init failed in ...`

Git could not commit in the project's source repository. Git's own message
follows the colon. Common causes: a pre-commit hook in an existing repository
rejected the commit, or the folder is not writable. (A missing git identity is
not a cause: the factory then commits as `Software Factory`.) Fix it, then
`factory retry`.

### Warning: `Code review round limit (N) reached with M blocking finding(s) open: ...`

Not a failure. The Code Review Agent still had critical/high findings after
`limits.max_review_rounds` fix rounds; the workflow continues and the findings
are listed in `review.md` for the release decision. Reject the release with
what to fix if they matter.

### `N blocking security finding(s) remain after N fix round(s)`

The deterministic scan still finds critical/high problems (possible secrets,
committed `.env`/key files) after `max_review_rounds` fix rounds. See
`security.md` for the files and lines. Remove the secrets from `source/` by
hand, then `factory retry` — your edits are committed automatically and the
scan runs again (with fresh fix rounds).

### `Final validation failed: ...`

After its fix budget the final run still fails. The message lists why, for
example `Final test run failed`, `Tasks not implemented: ...`,
`N uncommitted change(s) in the repository` or `FR-3 has unfinished tasks`.
See `validation.md`. Fix by hand in `source/`, then `factory retry` (edits are
committed automatically on retry). Uncommitted changes are counted **before**
the final run.

### Warning: `The test run itself created or changed files (tests should use temporary folders): ...`

Not a failure. The final test run wrote files into the repository (e.g. a
`data.json`). They are recorded as `validation.test_side_effects`; the release
guard and the external-change check on `factory continue` ignore exactly these
files, so the release is not refused because of them. Better tests write to a
temporary folder — reject the release with that request if you want it fixed.

### `The code changed after final validation, so it cannot be released as is`

`factory approve` at the release gate found that HEAD differs from the
validated commit, or that there are uncommitted changes other than the recorded
test side effects. Reject the release with a reason: your changes are then
built, tested, reviewed and validated again.

### Status `WAITING_FOR_CHANGE_REQUEST_APPROVAL`

The Coding Agent says an approved decision (architecture, technology or plan)
cannot work and proposes a change in `change-request.md`. Approve to re-open
that design stage (and everything after it) for revision and re-approval:

```bash
uv run factory approve <project>
```

Or reject so the agent completes the work within the approved decisions:

```bash
uv run factory reject <project> --reason "Keep the approved stack; mock the payment API"
```

### Release rejected, then build starts again

Rejecting at `WAITING_FOR_RELEASE_APPROVAL` is how you ask for changes: your
reason goes to the Coding Agent, then build, test, review, security and
validation run again, and you get a new release decision.

---

## Tests

### Tests fail or try to contact a model server

They should not: the suite uses a scripted provider and mocked HTTP and needs
neither Ollama, network nor any API key (the OpenAI-compatible tests set a
fake key themselves). Make sure the dev dependencies are installed and
that you run through uv:

```bash
uv sync
```

```bash
uv run pytest
```

## Still stuck?

Collect `factory doctor` output, `factory history <project> --json`, the
project's `errors.md`, and the tail of `<workspace>/logs/factory.log`
(secrets are already redacted).

## Related documents

- [Setup guide](setup_guide.md)
- [Configuration](configuration.md)
- [Local model setup](../models/local_model_setup.md)
- [Model configuration](../models/model_configuration.md)
- [Model selection](../models/model_selection.md)
- [Execution flow](../workflows/software_factory_execution_flow.md)

### `The folder of project '<id>' no longer exists`

The project's folder under `factory-workspace/projects/` was deleted outside
the factory, so the project is still listed but cannot continue. Remove it from
the list:

```bash
uv run factory delete <id>
```

(or **Remove from the list** in the web UI). Use `factory delete` (or the
**Delete project** button) to delete projects in the future — it removes the
list entry and the folder together.

### `<path> is not the top folder of a git repository`

For an existing-code project, give the app's own folder — the one that
contains `.git` — not a parent folder that holds several projects. For a folder
without git, run `git init` and make a first commit there.
