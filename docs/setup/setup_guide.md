# Setup guide

This guide takes you from a fresh checkout to a first project driven through
the whole lifecycle: design (requirements → architecture → technology →
implementation plan), build (coding → build → test → fix → code review →
security → final validation) and release. Follow it top to bottom.

With the shipped config the factory runs entirely on your machine: a Python
CLI (`factory`), a local model server (Ollama), and a workspace folder that
holds all project data, including the generated source code. Models can
instead be served by any OpenAI-compatible server or cloud API (LM Studio,
vLLM, DeepSeek, OpenAI, …) by changing only the config — see
[model configuration](../models/model_configuration.md#openai-compatible-servers-and-cloud-apis).

> **Keep this file current.** If you change installation, configuration or the
> CLI, update this guide in the same change (spec §73.7).

## First-run path at a glance

```text
uv sync                           create the Python environment
        ↓
review config/factory.yaml        providers, models, profiles, limits, allowed commands
        ↓
install + start Ollama            local model server (OLLAMA_MAX_LOADED_MODELS=1)
        ↓
ollama pull <models>              qwen2.5:14b (reasoning) + qwen2.5-coder:14b (coding)
        ↓
install project toolchains        e.g. Node.js for a React/Node project
        ↓
factory doctor --probe            verify everything, including the models
        ↓
factory evaluate                  check the model on 5 real factory tasks (~3.5 min)
        ↓
factory create <project>          create an isolated project
        ↓
factory start <project>           run the workflow until it needs you
        ↓
answer / approve                  clarification questions, four design gates
        ↓
(build phase runs by itself)      code, build, test, fix, review, security, validation
        ↓
factory approve <project>         release approval → release tag, COMPLETED
```

## 1. Prerequisites

| Requirement | Why | Check |
|---|---|---|
| macOS or Linux | The run lock uses `fcntl`, which is POSIX-only. Windows is not supported. | — |
| [uv](https://docs.astral.sh/uv/) | Creates the virtual environment, installs Python 3.11 if needed, installs dependencies. | `uv --version` |
| Git | Checked by `factory doctor`; every project's source code is a git repository the factory commits to. | `git --version` |
| Ollama | Local model server used by the shipped config. (Alternatively any OpenAI-compatible server or cloud API — provider type `openai_compatible`.) | `ollama --version` |
| RAM for the model | The shipped config uses 14B models (~9 GB each on disk) with a 32K context. See [model selection](../models/model_selection.md). | — |
| The project's toolchain | The factory runs the plan's setup/lint/build/test commands with real programs (e.g. `node`/`npm` for a React/Node project, `python`/`pytest` for Python). See [section 8](#8-programs-your-projects-need). | `node --version`, `python3 --version` |

Install uv if you do not have it (either command works):

```bash
brew install uv
```

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## 2. Python version

The factory requires **Python 3.11**. The version is pinned in
`.python-version` and `pyproject.toml` declares `requires-python = ">=3.11"`.
You do not need to install Python yourself: `uv sync` downloads a matching
interpreter if one is not already available.

## 3. Virtual environment and dependencies

From the repository root:

```bash
uv sync
```

This creates `.venv/` and installs both runtime dependencies (LangGraph,
langgraph-checkpoint-sqlite, Pydantic, Typer, Rich, httpx, PyYAML,
langchain-core) and the `dev` dependency group (pytest, respx, ruff, mypy,
types-pyyaml). The lock file is `uv.lock`; commit changes to it together with
`pyproject.toml`.

Run every factory command through uv so it uses this environment:

```bash
uv run factory --help
```

```bash
uv run factory version
```

(If you activate the environment with `source .venv/bin/activate`, you can
drop the `uv run` prefix.)

## 4. Environment configuration

Copy the example environment file:

```bash
cp .env.example .env
```

`.env.example` lists the variables the factory knows about:

| Variable | Purpose |
|---|---|
| `FACTORY_CONFIG` | Optional absolute path to a config file (see [configuration](configuration.md#where-the-config-file-is-found)). |
| `DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY` | API keys for cloud providers. A key is read only when a provider's `api_key_env` names that variable (e.g. the commented `deepseek` example in `config/factory.yaml` uses `DEEPSEEK_API_KEY`); any variable name works. Not needed for the shipped local config. |

Every CLI command loads `.env` from the **current working directory** before
reading the config, so run the CLI from the repository root. The format is one
`KEY=value` per line; `export KEY=value` is also accepted, surrounding quotes
are stripped, and blank lines and `#` comments are ignored. Variables already
set in your shell always win over `.env`. Values are never logged.

For a purely local setup with the shipped config you need no environment
variables at all. `.env` is git-ignored — never commit it.

## 5. Factory configuration

The configuration file is `config/factory.yaml`. Review it before the first
run; the parts you are most likely to change are:

- `factory.workspace` — where projects, logs and the database live
  (default `../factory-workspace`, i.e. `factory-workspace/` in the repo root).
- `models.*.model` — the Ollama model names. They must fit your machine.
- `providers.ollama.base_url` — if Ollama is not on `http://localhost:11434`.
- `providers` — to use LM Studio, another OpenAI-compatible server or a cloud
  API instead of (or besides) Ollama, enable one of the commented
  `openai_compatible` examples (`lmstudio`, `deepseek`) and point the models
  and profiles at it ([model configuration](../models/model_configuration.md#openai-compatible-servers-and-cloud-apis)).
- `security.allowed_commands` — the programs the factory may run for a project
  (shipped: `git, python, python3, pip, pytest, ruff, uv, node, npm, npx`).
- `limits.max_debug_iterations` / `limits.max_review_rounds` — how many
  automatic fix attempts the build phase makes before asking you.

Relative paths in the file resolve against the directory **containing the
config file**, not your shell's current directory. Every key is documented in
[configuration.md](configuration.md).

The CLI finds the config in this order:

1. `--config` / `-c` (a global option, placed **before** the command),
2. `$FACTORY_CONFIG`,
3. `./config/factory.yaml` relative to the current directory.

So from the repository root no flag is needed. From anywhere else:

```bash
uv run --project /path/to/software-factory factory -c /path/to/software-factory/config/factory.yaml doctor
```

## 6. Local model server

Install and start Ollama, then download the models named in the config. Full
instructions, including how to verify each step, are in
[local model setup](../models/local_model_setup.md). The short version on
macOS:

```bash
brew install ollama
```

A Homebrew install does **not** start the server automatically. Either run it
in its own terminal (it stops when you close that terminal):

```bash
OLLAMA_MAX_LOADED_MODELS=1 ollama serve
```

or register it as a background service that also starts at login (then set
the variable for the service, as described in
[local model setup](../models/local_model_setup.md)):

```bash
brew services start ollama
```

`OLLAMA_MAX_LOADED_MODELS=1` keeps only one model in memory: the factory
switches between two 14B models, and two loaded at once do not fit next to
everything else on a 32 GB machine (measured ~15 GB for one 14B model at 32k
context, ~12 GB at 16k).

In another terminal, pull the shipped config's two models (user decision
2026-09-30): `qwen2.5:14b` serves the `reasoning` profile (design agents) and
is the fallback for `coding`; `qwen2.5-coder:14b` serves the `coding` profile
(Coding, Testing and Code Review agents):

```bash
ollama pull qwen2.5:14b
```

```bash
ollama pull qwen2.5-coder:14b
```

If only `qwen2.5:14b` is pulled the workflow still runs (the coding profile
falls back to it, doctor shows WARN), but the coder model is what passed
`factory evaluate --case code_generation`. How profiles map to models:
[configuration](configuration.md#profilesname).

```bash
ollama list
```

Automatic model download is **Planned — not implemented yet**; you always pull
models yourself.

If your machine has less than ~16 GB of RAM, change the model names first — see
[model selection](../models/model_selection.md) and
[model configuration](../models/model_configuration.md).

## 7. Workspace and database

There is **no initialisation step**. The workspace directory and the SQLite
project registry are created automatically the first time the factory needs
them (any project command, or `factory doctor`).

```text
<workspace>/                      default: factory-workspace/ in the repo root
├── factory.db                    SQLite project registry (ids, names, status, paused flag)
├── logs/
│   └── factory.log               JSON lines, secrets redacted
└── projects/
    └── <project-id>/             one isolated folder per project
        ├── project.md, progress.md, decisions.md, errors.md, state.json
        ├── requirements-input.md, requirements.md/.json, architecture.md/.json,
        │   technology-stack.md/.json, implementation-plan.md/.json
        ├── test-results.md, test-analysis.md, review.md, security.md,
        │   validation.md, change-request.md, release-notes.md   (build phase)
        ├── execution-history.json
        ├── clarification-questions.md, clarification-answers.json
        ├── source/               the generated code: its own git repository (new projects)
        ├── artifacts/            every revision of every stage artifact (<stem>.revN.json)
        │   └── tool-runs/        full log of every project command (NNN-<step>.log)
        └── checkpoints/
            ├── workflow-checkpoints.sqlite   LangGraph checkpoints for this project only
            └── run.lock                      prevents two processes running one project
```

`factory-workspace/`, `*.db` and `*.sqlite` are git-ignored. To start over,
stop any running command and delete the workspace folder. Details:
[persistence architecture](../architecture/persistence_architecture.md).

## 8. Programs your projects need

In the build phase the factory runs the approved plan's commands (`setup`,
`format`, `lint`, `build`, `test`, `package`, `audit`) directly — no shell, so
no `&&`, `|` or `cd`. Each command's program must be:

1. **listed in `security.allowed_commands`** — `sh`, `bash`, `zsh`, `sudo`,
   `su`, `rm`, `curl` and `wget` are always refused, even if listed; and
2. **installed and on your `PATH`** — your normal `PATH`, not the factory's.
   Project commands run without the factory's own Python virtual environment
   (`security.isolate_project_environment: true`, the default), so the
   factory's `python`/`pytest` do not count: a Python project needs Python and
   its tools installed on your machine (for example through `uv` or the
   project's own `setup` command).

The Technology Selection and Implementation Planning agents are told your
operating system and which allowed programs are installed and which are not,
so they plan with what you have. Still, install the toolchain you expect
**before** the build phase — for example Node.js (which provides `node`,
`npm`, `npx`) for a React/Node project:

```bash
brew install node
```

Before writing any code the factory checks every program the plan's commands
need. If one is missing, the project fails immediately with
`The approved project commands need programs that are not available: ...`;
install it (or add it to `allowed_commands`), then `factory retry`.

Setup commands such as `npm install` or `pip install` download packages, so
the build phase needs **network access**. `security.network_enabled: false`
is not enforced by any tool yet; it does not block these downloads.

## 9. Git

Git must be on your `PATH`; `factory doctor` fails the **Git** check otherwise.

- **New projects:** the factory creates `projects/<id>/source/`, runs
  `git init`, adds a `.gitignore` (`node_modules/`, `.venv/`, `__pycache__/`,
  `dist/`, `build/`, `.env`, …) and makes an initial commit. After each task's
  tests pass it commits (`<task id>: <task title>`); fixes after review,
  security or validation are committed as `fix(<phase>): ...`. A release adds
  an annotated tag `release-1`, `release-2`, ….
- **Existing projects** (`factory create ... --repository <path>`): the
  repository must be a clean git repository (no uncommitted changes). The
  factory works on a branch `factory/<project-id>` in that repository.

Commits use your git identity if `user.email` is configured; otherwise they
are authored as `Software Factory <software-factory@localhost>`. Commit and
tag signing are disabled for the factory's own git calls.

## 10. Health check: `factory doctor`

```bash
uv run factory doctor
```

Checks, in order: Python, Dependencies, Configuration (a `reasoning` profile
must exist), Prompts (`system.md` + `task.md` for `requirement`,
`architecture`, `technology_selection`, `implementation_planning`, `coding`,
`testing` and `code_review`), Workspace (writable), Database, Git, each
enabled provider (can the Ollama server be reached? for an `openai_compatible`
provider: does `GET {base_url}/models` answer, and is the API key accepted?),
each configured model,
and one **Profile** row for each profile the agents use (`reasoning` and
`coding`).

- **Model rows:** an installed model is PASS with parameter size,
  quantization, context length and declared capabilities (WARN if its size
  exceeds 70% of your RAM). A missing model is always a **WARN**, with a note
  saying how it is used, e.g. `(preferred for reasoning; preferred for coding)`
  for the shipped `local_model`, `(fallback for coding)` or
  `(not used by any profile)`.
- **Profile rows:** PASS `uses <model>` when the preferred model is
  installed; WARN `preferred model missing; will use fallback <model>` (only
  with `fallback_models` configured — the shipped config has none); FAIL
  `no installed model` (none of its preferred/fallback models is pulled) or
  `not configured`.

Then run the live probe, which sends a tiny structured-output request through
the `reasoning` and `coding` profiles:

```bash
uv run factory doctor --probe
```

Results are PASS, WARN, FAIL or SKIP; hints appear under the detail with `→`.
Model and profile checks are skipped when their provider is unreachable, and
the probe is skipped entirely if any provider is unreachable. The command
exits with code 1 if any check FAILs.

With the shipped config and both models pulled, doctor passes (abridged):

```text
Check                         Result  Detail
Python                        PASS    3.11.x
Dependencies                  PASS    langgraph ...; pydantic ...
Configuration                 PASS    /…/config/factory.yaml (3f2a…)
Prompts                       PASS    /…/prompts
Workspace                     PASS    /…/factory-workspace
Database                      PASS    SQLite 3.x at /…/factory-workspace/factory.db
Git                           PASS    git version 2.x
Provider: ollama              PASS    2 model(s) installed
Model: local_model            PASS    qwen2.5:14b · 14.8B · Q4_K_M · ctx 32768 · caps: reasoning, coding, structured_output
Model: local_coder            PASS    qwen2.5-coder:14b · 14.8B · Q4_K_M · ctx 32768 · caps: coding, structured_output
Profile: reasoning            PASS    uses qwen2.5:14b
Profile: coding               PASS    uses qwen2.5-coder:14b
Structured output (reasoning) PASS    qwen2.5:14b answered 5 in … ms
Structured output (coding)    PASS    qwen2.5-coder:14b answered 5 in … ms
```

## 11. Evaluate the model: `factory evaluate`

The probe only shows the model can return JSON. Before relying on a model,
check it can do the factory's actual work:

```bash
uv run factory evaluate
```

It runs the real Requirement, Architecture, Technology Selection and Coding
agents (real prompts, the configured profiles) on five fixed cases and checks
each answer with code; the `code_generation` case writes a small module and
its tests to a temporary folder and runs `python -m unittest -v`, which must
run at least one test and exit 0. Expected with `qwen2.5:14b` for the reasoning
cases on an M1 Pro 32 GB (about 3.5 minutes in total); `code_generation` also
passes with the shipped coder model `qwen2.5-coder:14b`:

```text
Running 5 evaluation case(s) with the configured models…
  PASS requirement_extraction (reasoning, …s): … FR, … NFR
  PASS ambiguity_detection (reasoning, …s): asked … required question(s)
  PASS architecture_reasoning (reasoning, …s): … components, all FRs mapped
  PASS technology_fit (reasoning, …s): chose: …
  PASS code_generation (coding, …s): generated tests pass (Ran … tests in …s)

5/5 passed. Report saved in /…/factory-workspace/evaluations
```

Run a single case with `--case <name>` (repeatable). The command exits with
code 1 if any case fails; errors count as failures. The JSON report is
`<workspace>/evaluations/evaluation-<timestamp>.json`. What each case checks:
[model selection](../models/model_selection.md#verify-with-factory-evaluate);
FAIL lines: [troubleshooting](troubleshooting.md#factory-evaluate-reports-fail).

## 12. Run the tests

The test suite needs **no model server**: agents are tested with a scripted
provider, and the Ollama and OpenAI-compatible adapters with mocked HTTP
(respx). No API key is needed; the OpenAI-compatible tests set a fake one.

```bash
uv run pytest
```

Lint and type-check:

```bash
uv run ruff check src tests
```

```bash
uv run mypy
```

## 13. Run the factory: first project

> **Prefer a browser?** Everything below can also be done with buttons and
> forms: run `uv run factory ui` and follow the [Web UI guide](web_ui_guide.md).
> The steps here use the terminal.

The repository ships an example in `examples/employee-management/`. Its
requirements are deliberately incomplete so you see the clarification flow.

### Design phase

Create the project (the id is the slugified name, here `employee-management`):

```bash
uv run factory create "Employee Management" --requirements-file examples/employee-management/requirements.md
```

You can also pass `--requirements "text"`, or create without requirements and
add them later with `factory requirements <project> --file <path>` (or
`--text`) — only before the workflow starts. `--repository <path>` creates an
existing-project instead of a greenfield one.

Start the workflow. It runs until it needs a human; local models can take a few
minutes per stage:

```bash
uv run factory start employee-management
```

If the Requirement Agent asks questions, list and answer them (answers are
saved immediately), then continue:

```bash
uv run factory questions employee-management
```

```bash
uv run factory answer employee-management Q1 "A web application"
```

```bash
uv run factory continue employee-management
```

At each approval gate, review the Markdown artifact the status output points to
(`requirements.md`, `architecture.md`, `technology-stack.md`,
`implementation-plan.md` in the project folder), then approve or reject:

```bash
uv run factory approve employee-management
```

```bash
uv run factory reject employee-management --reason "Use PostgreSQL, not SQLite"
```

A rejection sends the artifact back to its agent for a revision. Check the
plan's **commands** section before approving it: those exact commands will be
run in the build phase, and their programs must be installed (section 8).

### Build phase

Approving the implementation plan continues straight into the build phase in
the same command. For each task, in dependency order:

1. the **Coding Agent** writes the files into `source/`;
2. the factory runs the plan's `setup` (again only when a dependency manifest
   changed), `format`, `lint` and `build` commands, then `test`;
3. if a command fails, the **Testing Agent** analyses the output and the
   Coding Agent fixes the code (status `BUG_FIXING`). In each round it proposes
   up to `limits.fix_candidates` (3) fixes; each is applied and really
   built and tested, and the first that passes is kept. Build and test then run
   again — at most `limits.max_debug_iterations` (5) rounds. If the same
   failure comes back 3 times in a row the project stops early with
   `No progress: …` so you can give a hint (below);
4. when the tests pass, the task is committed.

Only real exit codes decide pass or fail. Every command's full output is in
`artifacts/tool-runs/`; the latest run is summarised in `test-results.md`.

After the last task:

- **Code review** — the Code Review Agent reviews the diff; critical/high
  findings go back to the Coding Agent for up to `limits.max_review_rounds`
  (2) fix rounds (`review.md`).
- **Security validation** — a deterministic scan of tracked files for secrets
  and sensitive files, plus the plan's `audit` command if it has one; blocking
  findings get up to `max_review_rounds` fix rounds (`security.md`).
- **Final validation** — reruns setup (if needed), lint, build, test and
  package, checks that every task is done, the repository was clean before
  the run and each requirement traces to a task (`validation.md`). Files the
  test run itself writes into the repository are only a warning (tests should
  use temporary folders) and do not block the release. Failures go back for fixes
  within the `max_debug_iterations` budget.

If the Coding Agent decides an approved decision (architecture, technology or
plan) cannot work, it raises a **change request** instead of code and the
project waits in `WAITING_FOR_CHANGE_REQUEST_APPROVAL`. Review
`change-request.md`, then approve (re-opens that design stage and everything
after it) or reject (the agent must finish within the approved decisions):

```bash
uv run factory approve employee-management
```

```bash
uv run factory reject employee-management --reason "Keep SQLite; use a file-based DB in tests"
```

### Release

When final validation passes the project waits in
`WAITING_FOR_RELEASE_APPROVAL`. Review `validation.md`, `review.md` and
`security.md`, and try the code in `source/` yourself. Then approve the
release:

```bash
uv run factory approve employee-management
```

The factory tags the release (`release-1`) in the source repository, writes
`release-notes.md` and the project is `COMPLETED`. To send it back instead,
reject with what to fix; that starts a new fix → build → test → review cycle:

```bash
uv run factory reject employee-management --reason "The login page does not validate empty passwords"
```

### Other useful commands

```bash
uv run factory list
```

```bash
uv run factory status employee-management
```

```bash
uv run factory history employee-management
```

```bash
uv run factory pause employee-management
```

```bash
uv run factory retry employee-management
```

When a stage fails the project is `FAILED`; `factory status` and `errors.md`
say why. Fix the cause, then `factory retry` — it reruns the failed step with
a fresh fix budget. If the agents are stuck on a bug (for example
`No progress: the same failure came back 3 times …`), read `test-results.md`
and `test-analysis.md` and tell them what you think the real problem is:

```bash
uv run factory retry employee-management --hint "JSON object keys come back as strings after loading; convert ids to int"
```

In the build phase the hint goes to the Testing and Coding agents until the
failing work passes; for a failed design stage it becomes that agent's
revision feedback. A hint is only accepted when the project has failed. `Ctrl+C` is safe: progress up to the last completed step
is checkpointed and `factory continue` resumes it. The whole flow is described
in [the execution flow](../workflows/software_factory_execution_flow.md).

## 14. Common setup problems

| Symptom | Fix |
|---|---|
| `No factory configuration found` | Run from the repo root, or pass `-c`, or set `FACTORY_CONFIG`. |
| `Cannot reach Ollama at http://localhost:11434` | Start `ollama serve` or `brew services start ollama`. |
| `Model: … WARN … is not installed` | `ollama pull <model>`, or ignore it if the profile rows still PASS/WARN. |
| `Profile: <name> FAIL — no installed model` | Pull one of the models the hint lists. |
| `Dependencies FAIL` | `uv sync`. |
| `Prompts FAIL` | `factory.prompts_dir` must point at the repo's `prompts/` folder. |
| Stage fails with a timeout | Raise `providers.<name>.timeout_seconds` or use a smaller model. |
| `Provider '<name>' requires environment variable <VAR>` | Export the API key variable (or add it to `.env`). |
| `Provider: <name> FAIL — authentication failed — check the API key` | Wrong or expired key in the variable named by `api_key_env`. |
| `factory evaluate` shows `FAIL` | See [troubleshooting](troubleshooting.md#factory-evaluate-reports-fail); re-run the single case, or choose a stronger model. |
| `The approved project commands need programs that are not available` | Install the listed programs (or allow them), then `factory retry`. The factory's own virtual environment does not count (`security.isolate_project_environment`). |
| `No progress: the same failure came back 3 times …` | Read `test-results.md` / `test-analysis.md`, then `factory retry <project> --hint "…"`. |
| Machine swaps or Ollama is very slow with two models | Start Ollama with `OLLAMA_MAX_LOADED_MODELS=1`. |

Full list with exact error messages: [troubleshooting](troubleshooting.md).

## Related documents

- [Web UI guide](web_ui_guide.md)
- [Configuration reference](configuration.md)
- [Troubleshooting](troubleshooting.md)
- [Local model setup](../models/local_model_setup.md)
- [Model configuration](../models/model_configuration.md)
- [Model selection](../models/model_selection.md)
- [Execution flow](../workflows/software_factory_execution_flow.md)
- [Documentation index](../README.md)
