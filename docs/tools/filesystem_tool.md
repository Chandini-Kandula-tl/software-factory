# Filesystem Tool

**Code:** [`src/software_factory/tools/filesystem.py`](../../src/software_factory/tools/filesystem.py) · **Tests:** `tests/unit/test_tools.py` · **Spec:** §18, §68.12

## Purpose

Read and write files **inside a single root directory** (the project workspace). Every
path goes through `resolve()`, which confines access to the root.

## Current usage

- Tool name `filesystem` (`ToolName.FILESYSTEM`). `ToolKit(workspace_root, security)` builds `FilesystemTool(workspace_root)` for it (`tools_for(["filesystem"])` or `require("filesystem", allowed)`).
- The [Coding Agent](../agents/coding_agent.md) declares `allowed_tools=(filesystem,)`. It does not call the tool itself: the build-phase workflow (`_apply_changes` in [`development_nodes.py`](../../src/software_factory/orchestration/nodes/development_nodes.py)) gets it with `ToolKit(source, security).require(FILESYSTEM, coding agent's allowed_tools)` and applies each proposed `FileChange` in `CodeChanges.all_files` (`files` + `test_code`): `write_text` for `create`/`update`, and `delete` for `delete`. If the Coding Agent did not declare `filesystem`, `require` raises `ToolPermissionError` ("Tool 'filesystem' is not allowed here") and the stage fails, so least privilege is enforced.
- `prepare_development` also builds a `FilesystemTool` directly to write the default `.gitignore` (a workflow action, not on an agent's behalf).
- Design-phase agents have no tools.
- The factory's own project artifacts are written by `ArtifactStore`, not by this tool.

## API

```python
FilesystemTool(root: Path, max_file_bytes: int = 2_000_000)
resolve(relative) -> Path            # confinement check, see below
exists(relative) -> bool
read_text(relative) -> str           # UTF-8
write_text(relative, content) -> Path  # creates parent dirs
delete(relative) -> bool             # True if a file was removed, False if it did not exist
list_dir(relative=".") -> list[str]  # sorted, root-relative, dirs end with "/"
```

## Safety rules

| Rule | Behavior |
|---|---|
| Absolute path (`/etc/passwd`) | `PathOutsideWorkspaceError` |
| Traversal (`../x`, `a/../../x`) | `PathOutsideWorkspaceError` — the path is resolved and must be the root or inside it |
| Symlink escaping the root | `PathOutsideWorkspaceError` — `Path.resolve()` follows symlinks before the check |
| File larger than `max_file_bytes` on read | `ToolPermissionError` |
| Content larger than `max_file_bytes` (UTF-8 bytes) on write | `ToolPermissionError` |
| `delete` on a directory | `ToolPermissionError` ("Refusing to delete a directory"); only single files are deleted |

`PathOutsideWorkspaceError` is a subclass of `ToolPermissionError`, which is a
`FactoryError` (so the CLI prints an actionable message instead of a traceback).

## Examples

```python
fs = FilesystemTool(workspace)
fs.write_text("src/app.py", "print(1)\n")
fs.list_dir("src")            # ["src/app.py"]
fs.delete("src/app.py")       # True
fs.delete("src")              # ToolPermissionError: refusing to delete a directory
fs.read_text("../secret")     # raises PathOutsideWorkspaceError
```

## Known limitations

- No read-only mode and no per-path allow/deny lists: whoever holds the tool can write anywhere under the root.
- No move/rename and no directory deletion. A rename is a `delete` plus a `create`. Directories left empty remain on disk (git ignores them).
- Deletions are recoverable only through git history (the previous commit).
- Reads assume UTF-8 text; binary files are not supported.
- The size cap is not configurable through `SecurityConfig` (`ToolKit` uses the default 2 MB).
- The check-then-use is not atomic (a symlink swapped in after `resolve()` is a theoretical race).

See also: [security architecture](../architecture/security_architecture.md).
