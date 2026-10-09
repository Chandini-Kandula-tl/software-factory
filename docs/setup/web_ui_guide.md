# Web UI guide

The Software Factory has a local web interface for people who prefer buttons
and forms to terminal commands. It does everything the CLI does for a project —
create, answer questions, approve or reject, retry with guidance, pause,
download the result — and shows the project's documents as readable pages.

It runs **only on your own computer** (`127.0.0.1`); nothing is reachable from
other machines, and no data leaves your Mac except what your configured model
provider receives (none, with the default local Ollama setup).

## Start it

Make sure the model server is running first (see
[local model setup](../models/local_model_setup.md)):

```bash
OLLAMA_MAX_LOADED_MODELS=1 ollama serve
```

Then, from the repository root:

```bash
uv run factory ui
```

Your browser opens at `http://127.0.0.1:8000`. Keep the terminal window open —
closing it (or pressing Ctrl+C) stops the web UI. Work already checkpointed is
never lost; `factory continue` or the **Continue** button resumes it later.

Options:

**Existing code:** the folder you give on the New project page must be the
top folder of one git repository (the folder that contains `.git`), for
example `~/Documents/GitHub/my-app` — not a folder that holds several
projects.

| Option | Meaning |
|---|---|
| `--port 8765` | Use another port (e.g. if 8000 is taken) |
| `--no-browser` | Do not open a browser window automatically |

## Pages

| Page | Address | What you do there |
|---|---|---|
| Projects | `/` | See every project and its status; open one; create a new one |
| New project | `/new` | Give a name and describe the app in plain words; optionally point at an existing code folder. **Create and start** begins immediately |
| Project | `/p/<project-id>` | The page adapts to what the factory needs from you (below) |
| Document | `/p/<project-id>/doc/<name>` | Read requirements, architecture, plan, test results, review, security, validation, release notes… |
| History | `/p/<project-id>/history` | Every step the agents and tools took, with model, result and errors |
| Download | `/p/<project-id>/download` | The generated code as a `.zip` (without `.git` and dependency folders) |
| Health check | `/doctor` | Same checks as `factory doctor`; **Also test the models** runs the live probe |

### The project page

At the top: the project's stages with ticks for completed ones, and task
progress during coding. On the right: the project documents and recent
activity. The main panel shows exactly one thing — what happens next:

| Situation | What you see | Your action |
|---|---|---|
| Not started | The requirements text | **Start** |
| The factory is working | A spinner, the current step, live updates every few seconds | Wait, or **Pause after the current step**. You may close the page; the work continues |
| Clarification questions | A form with one box per question (required ones marked) | Answer and **Submit answers** |
| Approval (requirements, architecture, technology, plan) | The document, rendered | **Approve**, or write what should change and **Reject and revise** |
| Plan approval with problems | A yellow warning listing commands that cannot run on this computer | Reject the plan with what to change, or install the programs first |
| Change request | The Coding Agent's request, its impact analysis and what is affected | Approve (that design stage is redone) or reject (the agent continues within the approved design) |
| Release approval | The final validation, with links to code review, security, test results and the download | Try the code, then **Approve** — or reject with what to fix |
| Needs your help (failed) | What stopped and why, with links to test results and the failure analysis | Optionally write **Guidance for the AI** (what you think the real problem is), then **Retry**; or cancel the project |
| Completed | Release notes, **Download the app (.zip)** and **Update this app** | Use the app. To change it later, describe the change under **Update this app**: the same project continues as version 2, 3, … (requirements, design and plan are revised for the change and approved again, then only the change is coded, tested and released). Earlier versions' documents stay available under **Earlier versions** |
| Interrupted / older project | "Ready to continue" | **Continue** |
| Folder missing (removed outside the factory) | A warning; the project cannot continue | **Remove from the list** |

**Deleting a project:** the **Delete project** box on the right of the project
page (shown when nothing is running) removes the project's documents, history
and — for new apps — the generated code, after a confirmation. Download the app
first if you want to keep it. For existing-code projects your repository is
never touched. Deleting a project's folder by hand leaves it in the list as
"Folder missing"; use **Remove from the list** there.

If an action stops with an error (for example the model server is not
running), a red box at the top says what happened and how to fix it.

## Tips

- **Long steps are normal.** A local model can take 1–3 minutes per step and
  more when it writes code. The page refreshes itself; you can leave it.
- **Good rejections are specific.** "Add a command-line entry point; use only
  the standard library" works better than "not good".
- **Hints help small models.** When a project stops after several failed fixes,
  one sentence describing the real problem (for example "JSON keys come back
  as strings — convert ids with int() when loading") usually gets it moving.
  The hint is used until that piece of work passes.
- **Terminal and web UI can be mixed.** Both use the same project files; the
  factory lets only one of them advance a project at a time.

## Security notes

- Listens on `127.0.0.1` only.
- Every form carries a random token created when the server starts, so another
  website open in your browser cannot trigger factory actions.
- Documents are Markdown written by the models; raw HTML in them is never
  rendered, so model output cannot run scripts in your browser.
- Only the known project documents can be opened — not arbitrary files.

Details: [security architecture](../architecture/security_architecture.md).

## Troubleshooting

| Problem | Fix |
|---|---|
| `address already in use` | Another program uses the port: `uv run factory ui --port 8765` |
| Red box: "Cannot reach Ollama" | Start the model server (`ollama serve`), then press **Retry** |
| "Invalid form token — reload the page" | The web UI was restarted; reload the page and try again |
| The page shows old styling after an update | Reload the page (the stylesheet is versioned, a normal reload is enough) |
| "Project … is already running in another process" | A terminal command is advancing this project; wait for it or pause it |

More: [troubleshooting](troubleshooting.md).
