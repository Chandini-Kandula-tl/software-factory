# Git Tool

**Code:** [`src/software_factory/tools/git.py`](../../src/software_factory/tools/git.py) · **Tests:** `tests/unit/test_tools.py` · **Spec:** §18, §68.12

## Purpose

Policy-checked git operations on one repository. Safe operations run directly;
destructive ones require an explicit human **approval reference**.

## Current usage

- Tool name `git`. `ToolKit` builds `GitTool(workspace_root, enabled=security.shell_enabled)`, so disabling the shell also disables git.
- **No agent is granted it.** The build-phase workflow uses it directly ([`development_nodes.py`](../../src/software_factory/orchestration/nodes/development_nodes.py) for prepare/test/change request/retry, [`quality_nodes.py`](../../src/software_factory/orchestration/nodes/quality_nodes.py) for review/security/final validation/release; `GitTool(source, enabled=security.shell_enabled)`):
  - **prepare:** for greenfield projects, `init` whenever `source/.git` is missing (even if an enclosing directory is a git repository, e.g. the factory's own checkout), then `rev-parse --show-toplevel` must equal `source/` (else `FactoryError` "… is not the root of its own git repository"), then an initial commit (`chore: initialize repository (Software Factory)`) if there is no `head()` yet. For existing repositories: `state()` (must be a clean repo), then `branch_exists` + `checkout`/`create_branch` for `factory/<project_id>`, then `head()` as `base_commit`.
  - **test:** `commit` after the test command passes (`T-n: <title>` or `fix(<phase>): <summary>`); the new `head()` is stored as `development.last_commit`.
  - **change request:** `diff --name-only <base_commit>..HEAD` for the request's `changed_files`.
  - **code review:** `diff <base_commit>..HEAD` (or `show HEAD`) as the reviewer's input.
  - **security validation:** `tracked_files()` for the secret scan.
  - **final validation:** `changed_paths()` **before** the final run (any path is a failure: "N uncommitted change(s) in the repository"), and again after it; paths that appeared only after the run are the test run's own side effects, stored as `validation.test_side_effects` and reported as a warning. `state()`'s commit is stored as `validation.head_commit`.
  - **retry** (`prepare_retry`, all tasks done): `commit("chore: manual changes before retry")` if the tree is dirty.
  - **release:** `tag --list release-*` and then `tag("release-N", ...)`.
- The [repository analysis tool](repository_analysis_tool.md) calls `GitTool(root).state()` for existing projects.
- `FactoryService` inspects the generated repository: in `continue_` (`_check_external_changes`, compares HEAD from `state()` with `development.last_commit` and counts `changed_paths()` other than `validation.test_side_effects`) and before a release approval (`_require_validated_code`, `head()` must equal `validation.head_commit` and `changed_paths()` may contain only `validation.test_side_effects`).

## How it runs

`GitTool(repository, timeout_seconds=60.0, enabled=True)` wraps a private
`ShellTool(repository, ["git"], enabled=enabled, tool_name="git")`, so every call inherits
the [shell tool](shell_tool.md) behavior: disabled → `ToolPermissionError`, argv only,
cwd = the repository, scrubbed env, redacted/truncated output, timeout → `timed_out`
result. Results have `tool == "git"`.

Every policy-checked call (`run` and the helpers built on it) executes as
`git -c commit.gpgsign=false -c tag.gpgsign=false <args>`, so git never prompts for a
signing key.

**Commit identity:** if `git config --get user.email`, run in the target repository (so a repository-local identity counts), returns nothing, the call gets a
fallback identity through environment variables (`GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL`,
`GIT_COMMITTER_NAME`, `GIT_COMMITTER_EMAIL`):
`Software Factory <software-factory@localhost>`. Variables already set in the environment
are not overridden. When the user has an email configured, their identity is used
unchanged.

## Policy (`run(args, approval_reference=None)`)

| Category | Values | Behavior |
|---|---|---|
| Safe subcommands | `status`, `diff`, `log`, `rev-parse`, `branch`, `checkout`, `switch`, `add`, `commit`, `init`, `show`, `ls-files`, `tag` | Run (unless a dangerous flag makes them dangerous, e.g. `tag -d`). |
| Dangerous subcommands | `push`, `reset`, `clean`, `rebase`, `filter-branch`, `gc`, `prune`, `reflog`, `rm`, `stash` | Require `approval_reference`. |
| Dangerous flags (any position after the subcommand) | `--force`, `-f`, `-D`, `--hard`, `--delete`, `-d` | Make any command dangerous → require `approval_reference`. The argument right after `-m` / `--message` is free text and is **not** checked, so `commit -m "-f"` is fine. |
| Discarding checkout | `checkout` whose arguments include `--` or `.` (e.g. `checkout -- file.py`, `checkout .`) | Discards uncommitted changes → requires `approval_reference`. Plain `checkout <branch>` and `checkout -b <name>` stay safe. |
| Anything else (`config`, `remote`, `fetch`, `merge`, ...) | — | Always `ToolPermissionError` "not a permitted operation". |
| Empty args | — | `ToolPermissionError`. |

Without an approval reference, a dangerous call raises `ToolPermissionError` ("destructive
and requires explicit human approval"). The reference is only checked for presence; it is
not validated against stored approvals yet.

## Convenience methods

| Method | Runs |
|---|---|
| `init()` | `git init` |
| `status()` | `git status --porcelain` |
| `diff(*paths)` | `git diff [paths]` |
| `log(limit=10)` | `git log -<limit> --oneline` |
| `create_branch(name)` | `git checkout -b <name>` |
| `checkout(name)` | `git checkout <name>` |
| `commit(message, paths=(".",))` | `git add -A <paths>` then `git commit --allow-empty -m <message>` (stops if `add` fails). `-A` stages deletions too; `--allow-empty` lets a step with no file changes still produce its commit. |
| `head()` | `git rev-parse HEAD` → full SHA, or `None` (no commits yet / failure) |
| `tracked_files()` | `git ls-files` → list of paths |
| `changed_paths()` | `git status --porcelain --untracked-files=all` → sorted list of paths with uncommitted changes (modified, added, deleted or untracked; for a rename the new path; surrounding quotes stripped) |
| `tag(name, message)` | `git tag -a <name> -m <message>` (annotated tag) |
| `branch_exists(name)` | `git branch --list <name>` is non-empty |
| `state()` | Returns `GitState` (below) |

`state()` → `GitState` ([`domain/repository.py`](../../src/software_factory/domain/repository.py)):
`is_repository`, `branch`, `commit` (full SHA), `clean` (`None` if status failed),
`changed_files` (porcelain line count). Outside a work tree (or if git is not installed)
it returns `GitState(is_repository=False)`.

## Example

```python
git = GitTool(workspace)
git.init(); git.commit("first commit")
git.head()                                             # "3f2a…"
git.tag("release-1", "first release")                  # annotated tag, never signed
git.run(["reset", "--hard", "HEAD"])                   # ToolPermissionError
git.run(["reset", "--hard", "HEAD"], approval_reference="APPROVAL-123")  # runs
git.run(["checkout", "--", "a.txt"])                   # ToolPermissionError (discards changes)
git.run(["config", "user.name", "x"])                  # ToolPermissionError: not permitted
```

## Known limitations

- Discard detection covers `checkout` with `--` or `.` only; `git switch --discard-changes` or `git checkout <path>` without `--` (when the path is not `.`) are not detected.
- Flag matching is exact per argument: combined short flags (e.g. `-fd`) and `--force-with-lease` are not matched as flags (the subcommands where they matter, `clean` and `push`, are dangerous anyway).
- `GitTool` always allowlists `git` itself and does not consult `security.allowed_commands` (it does honor `security.shell_enabled` when built by `ToolKit`). The [repository analysis tool](repository_analysis_tool.md) builds its own `GitTool(root)` with the default `enabled=True`.
- `approval_reference` is only checked for presence; it is not verified against recorded approvals.
- `state()` runs `rev-parse` directly on the inner shell, so those calls do not get the `-c ...gpgsign=false` prefix (they do not need it).
- No remote operations other than `push` (dangerous); no network restriction beyond that (`security.network_enabled` is not enforced at OS level).
