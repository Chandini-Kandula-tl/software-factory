"""Coding Agent — implements one approved plan task, or fixes a reported problem.

It returns proposed file changes; the workflow validates and applies them with
the filesystem tool, then proves them with real build/test runs. It can raise
a change request instead of silently deviating from approved decisions.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import ClassVar

from software_factory.agents.base import AgentContext, AgentSpec, LLMAgent
from software_factory.domain.development import CodeChanges, FileAction
from software_factory.domain.errors import AgentOutputError
from software_factory.tools.base import ToolName


class CodingAgent(LLMAgent[CodeChanges]):
    spec: ClassVar[AgentSpec] = AgentSpec(
        name="coding-agent",
        version="1.0.0",
        purpose="Implement approved plan tasks and fix reported defects by proposing complete "
        "file changes that follow the approved architecture and technology.",
        llm_profile="coding",
        prompt_dir="coding",
        output_schema=CodeChanges,
        required_inputs=("mode", "task", "requirements", "architecture", "technology_selection"),
        optional_inputs=(
            "commands",
            "file_tree",
            "relevant_files",
            "fix_details",
            "failure_analysis",
            "change_request_feedback",
            "human_guidance",
            "allow_edits",
        ),
        # Writes are applied by the workflow through the filesystem tool.
        allowed_tools=(ToolName.FILESYSTEM,),
        capabilities=("code_generation", "debugging"),
    )

    def validate_output(self, output: CodeChanges, context: AgentContext) -> list[str]:
        warnings = _drop_duplicate_paths(output)
        for change in output.all_files:
            path = PurePosixPath(change.path)
            if path.is_absolute() or ".." in path.parts or not change.path.strip():
                raise AgentOutputError(f"File path must be relative and inside the repo: {path}")
            if path.parts and path.parts[0] == ".git":
                raise AgentOutputError(f"Changes inside .git are not allowed: {path}")
            if change.action is not FileAction.DELETE and not change.content.strip():
                warnings.append(f"{path} is written with empty content")
        if output.edits and context.inputs.get("allow_edits") is False:
            raise AgentOutputError(
                "`edits` are not allowed in this attempt because an earlier edit could not be "
                "applied; return the complete changed files in `files` / `test_code` instead"
            )
        for edit in output.edits:
            path = PurePosixPath(edit.path)
            if path.is_absolute() or ".." in path.parts or (path.parts and path.parts[0] == ".git"):
                raise AgentOutputError(f"Edit path must be relative and inside the repo: {path}")
            if not edit.find:
                raise AgentOutputError(f"Edit for {path} has empty `find` text")
        if output.change_request and output.all_files:
            warnings.append("Change request raised; proposed file changes will not be applied")
        if not output.all_files and not output.edits and not output.change_request:
            warnings.append("No file changes proposed")
        return warnings


def _drop_duplicate_paths(output: CodeChanges) -> list[str]:
    """A path given twice (e.g. in both ``files`` and ``test_code``): keep the last one."""
    last = {change.path: index for index, change in enumerate(output.all_files)}
    duplicates = sorted({c.path for i, c in enumerate(output.all_files) if last[c.path] != i})
    if not duplicates:
        return []
    split = len(output.files)
    output.files = [c for i, c in enumerate(output.files) if last[c.path] == i]
    output.test_code = [c for i, c in enumerate(output.test_code, split) if last[c.path] == i]
    return [f"{path} was given more than once; the last version is used" for path in duplicates]
