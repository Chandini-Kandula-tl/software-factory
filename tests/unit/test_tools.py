"""Tool layer: filesystem safety, command restrictions, timeouts, git, repository analysis."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from software_factory.config.settings import SecurityConfig
from software_factory.tools.base import PathOutsideWorkspaceError, ToolPermissionError
from software_factory.tools.filesystem import FilesystemTool
from software_factory.tools.git import GitTool
from software_factory.tools.project_commands import TestTool, tool_for_step
from software_factory.tools.repository_analysis import analyze_repository
from software_factory.tools.shell import ShellTool
from software_factory.tools.toolkit import ToolKit

PYTHON = Path(sys.executable).name


# -- filesystem ------------------------------------------------------------------


def test_filesystem_reads_and_writes_inside_root(tmp_path):
    fs = FilesystemTool(tmp_path)
    fs.write_text("src/app.py", "print(1)\n")
    assert fs.read_text("src/app.py") == "print(1)\n"
    assert fs.list_dir("src") == ["src/app.py"]


@pytest.mark.parametrize("bad", ["../outside.txt", "a/../../outside.txt", "/etc/passwd"])
def test_filesystem_blocks_traversal(tmp_path, bad):
    fs = FilesystemTool(tmp_path / "root")
    (tmp_path / "root").mkdir()
    with pytest.raises(PathOutsideWorkspaceError):
        fs.write_text(bad, "x")


def test_filesystem_blocks_symlink_escape(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "secret.txt").write_text("secret")
    (root / "link").symlink_to(tmp_path / "secret.txt")
    with pytest.raises(PathOutsideWorkspaceError):
        FilesystemTool(root).read_text("link")


def test_filesystem_size_limit(tmp_path):
    with pytest.raises(ToolPermissionError):
        FilesystemTool(tmp_path, max_file_bytes=10).write_text("big.txt", "x" * 11)


# -- shell ---------------------------------------------------------------------


def test_shell_runs_allowlisted_command_and_reports_real_exit_code(tmp_path):
    shell = ShellTool(tmp_path, [PYTHON])
    ok = shell.run([sys.executable, "-c", "print('hello')"])
    assert ok.success and ok.exit_code == 0 and ok.stdout.strip() == "hello"
    bad = shell.run([sys.executable, "-c", "import sys; sys.exit(3)"])
    assert not bad.success and bad.exit_code == 3


def test_shell_rejects_unlisted_and_forbidden_commands(tmp_path):
    shell = ShellTool(tmp_path, ["python", "rm", "bash"])
    with pytest.raises(ToolPermissionError, match="allowlist"):
        shell.run(["ls"])
    with pytest.raises(ToolPermissionError):
        shell.run(["rm", "-rf", "/"])  # forbidden even when listed
    with pytest.raises(ToolPermissionError):
        shell.run(["bash", "-c", "echo hi"])


def test_shell_rejects_cwd_outside_root_and_when_disabled(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(PathOutsideWorkspaceError):
        ShellTool(root, [PYTHON]).run([sys.executable, "-V"], cwd=tmp_path)
    with pytest.raises(ToolPermissionError, match="disabled"):
        ShellTool(root, [PYTHON], enabled=False).run([sys.executable, "-V"])


def test_shell_timeout(tmp_path):
    result = ShellTool(tmp_path, [PYTHON], timeout_seconds=0.5).run(
        [sys.executable, "-c", "import time; time.sleep(5)"]
    )
    assert result.timed_out and not result.success
    assert "timed out" in result.stderr


def test_shell_scrubs_secret_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-live-abcdefghijklmnopqrstuvwxyz")
    result = ShellTool(tmp_path, [PYTHON]).run(
        [sys.executable, "-c", "import os; print(os.environ.get('OPENAI_API_KEY', 'absent'))"]
    )
    assert result.stdout.strip() == "absent"


def test_project_command_tool_success_comes_from_exit_code(tmp_path):
    shell = ShellTool(tmp_path, [PYTHON])
    failing = TestTool(shell).run([sys.executable, "-c", "raise SystemExit(1)"])
    assert failing.tool == "test" and not failing.success
    env = tool_for_step("setup", shell).run(
        [sys.executable, "-c", "import os; print(os.environ['CI'])"], extra_env={"CI": "true"}
    )
    assert env.tool == "package_manager" and env.stdout.strip() == "true"
    with pytest.raises(ToolPermissionError, match="Empty"):
        TestTool(shell).run([])
    with pytest.raises(ToolPermissionError, match="No tool"):
        tool_for_step("deploy", shell)


def test_toolkit_grants_only_allowed_tools(tmp_path):
    kit = ToolKit(tmp_path, SecurityConfig())
    assert kit.tools_for([]) == {}
    tools = kit.tools_for(["filesystem", "git", "test"])
    assert set(tools) == {"filesystem", "git", "test"}
    assert isinstance(tools["test"], TestTool)
    assert kit.require("filesystem", ["filesystem"]) is not None
    with pytest.raises(ToolPermissionError, match="not allowed"):
        kit.require("git", ["filesystem"])


# -- git -------------------------------------------------------------------------


@pytest.fixture
def repo(tmp_path, monkeypatch) -> GitTool:
    for key, value in {
        "GIT_AUTHOR_NAME": "Factory Test",
        "GIT_AUTHOR_EMAIL": "factory@example.test",
        "GIT_COMMITTER_NAME": "Factory Test",
        "GIT_COMMITTER_EMAIL": "factory@example.test",
    }.items():
        monkeypatch.setenv(key, value)
    git = GitTool(tmp_path)
    assert git.init().success
    return git


def test_git_operations(repo, tmp_path):
    assert repo.state().is_repository
    (tmp_path / "a.txt").write_text("one\n")
    assert repo.state().changed_files == 1
    assert repo.commit("first commit").success
    state = repo.state()
    assert state.clean and state.commit and len(state.commit) == 40
    assert repo.create_branch("feature/x").success
    assert repo.state().branch == "feature/x"
    (tmp_path / "a.txt").write_text("two\n")
    assert "+two" in repo.diff().stdout
    assert "first commit" in repo.log().stdout


def test_git_destructive_operations_need_approval(repo):
    for args in (["push"], ["reset", "--hard", "HEAD"], ["clean", "-fd"], ["branch", "-D", "x"]):
        with pytest.raises(ToolPermissionError):
            repo.run(args)
    with pytest.raises(ToolPermissionError, match="not a permitted"):
        repo.run(["config", "user.name", "x"])


def test_git_state_outside_repository(tmp_path):
    assert not GitTool(tmp_path).state().is_repository


# -- repository analysis ---------------------------------------------------------


def test_repository_analysis_detects_stack(tmp_path):
    (tmp_path / "web" / "src").mkdir(parents=True)
    (tmp_path / "web" / "package.json").write_text(
        '{"dependencies": {"react": "18", "next": "14"}, "devDependencies": {"jest": "29"}}'
    )
    (tmp_path / "web" / "src" / "index.ts").write_text("export {}")
    (tmp_path / "api").mkdir()
    (tmp_path / "api" / "main.py").write_text("")
    (tmp_path / "api" / "tests").mkdir()
    (tmp_path / "api" / "tests" / "test_main.py").write_text("")
    (tmp_path / "Dockerfile").write_text("FROM scratch")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "ignored.js").write_text("")

    analysis = analyze_repository(tmp_path)
    assert analysis.languages == {"Python": 2, "TypeScript": 1}
    assert {"React", "Next.js"} <= set(analysis.frameworks)
    assert "npm" in analysis.package_managers
    assert "Docker" in analysis.build_systems
    assert "api/tests/" in analysis.test_locations
    assert "api/main.py" in analysis.likely_entry_points
    assert "node_modules/" not in analysis.top_level_structure
    assert not analysis.git.is_repository


def test_git_checkout_discard_needs_approval_but_commit_message_flags_do_not(repo, tmp_path):
    (tmp_path / "a.txt").write_text("one\n")
    assert repo.commit("-f").success  # message text is not treated as a flag
    for args in (["checkout", "--", "a.txt"], ["checkout", "."]):
        with pytest.raises(ToolPermissionError):
            repo.run(args)


def test_git_obeys_shell_disabled(tmp_path):
    kit = ToolKit(tmp_path, SecurityConfig(shell_enabled=False))
    with pytest.raises(ToolPermissionError, match="disabled"):
        kit.tools_for(["git"])["git"].status()


def test_project_commands_do_not_see_the_factory_environment(tmp_path, monkeypatch):
    from software_factory.tools import shell as shell_module

    own_bin = tmp_path / "factory-venv-bin"
    own_bin.mkdir()
    tool = own_bin / "factory-only-tool"
    tool.write_text("#!/bin/sh\necho hi\n")
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", f"{own_bin}{os.pathsep}/usr/bin{os.pathsep}/bin")
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path))
    monkeypatch.setattr(shell_module, "factory_environment_bin", lambda: str(own_bin))

    assert shell_module.find_program("factory-only-tool", isolate=False)
    assert shell_module.find_program("factory-only-tool", isolate=True) is None
    env = shell_module.project_environment(isolate=True)
    assert str(own_bin) not in env["PATH"] and env["VIRTUAL_ENV"] is None
    assert shell_module.project_environment(isolate=False) == {}

    result = ShellTool(tmp_path, [PYTHON]).run(
        [sys.executable, "-c", "import os; print(os.environ.get('VIRTUAL_ENV', 'unset'))"],
        extra_env={"VIRTUAL_ENV": None},
    )
    assert result.stdout.strip() == "unset"
