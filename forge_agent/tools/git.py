"""Read-only Git inspection tools with bounded, shell-free subprocesses."""

from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any

from .core import ToolResult, ToolSpec
from .filesystem import Workspace


def _git(workspace: Workspace, arguments: list[str]) -> ToolResult:
    try:
        completed = subprocess.run(
            ["git", "-c", "core.quotepath=false", *arguments],
            cwd=workspace.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return ToolResult.failure(f"Git inspection failed: {exc}")
    output = completed.stdout
    if completed.stderr:
        output += ("\n" if output else "") + completed.stderr
    output = output.strip() or "(no output)"
    factory = ToolResult.success if completed.returncode == 0 else ToolResult.failure
    return factory(output, exit_code=completed.returncode)


def _optional_path(workspace: Workspace, args: dict[str, Any]) -> list[str]:
    requested = args.get("path")
    if not requested:
        return []
    resolved = workspace.resolve(requested, must_exist=True)
    return ["--", workspace.display(resolved)]


def build_git_tools(workspace: Workspace) -> list[ToolSpec]:
    object_schema = {"type": "object", "additionalProperties": False}

    def git_status(args: dict[str, Any]) -> ToolResult:
        return _git(workspace, ["status", "--short", "--branch"])

    def git_diff(args: dict[str, Any]) -> ToolResult:
        context = args.get("context_lines", 3)
        if not 0 <= context <= 20:
            return ToolResult.failure("context_lines must be between 0 and 20")
        arguments = [
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            f"--unified={context}",
        ]
        if args.get("staged", False):
            arguments.append("--cached")
        arguments.extend(_optional_path(workspace, args))
        return _git(workspace, arguments)

    def git_log(args: dict[str, Any]) -> ToolResult:
        limit = args.get("limit", 10)
        if not 1 <= limit <= 50:
            return ToolResult.failure("limit must be between 1 and 50")
        arguments = [
            "log",
            f"--max-count={limit}",
            "--date=iso-strict",
            "--pretty=format:%h %ad %an %s",
        ]
        arguments.extend(_optional_path(workspace, args))
        return _git(workspace, arguments)

    return [
        ToolSpec(
            "git_status",
            "Show the current branch and concise working-tree status without changing Git state.",
            {**object_schema, "properties": {}},
            git_status,
        ),
        ToolSpec(
            "git_diff",
            "Show an unstaged or staged Git diff, optionally limited to one workspace path.",
            {
                **object_schema,
                "properties": {
                    "staged": {"type": "boolean"},
                    "path": {"type": "string"},
                    "context_lines": {"type": "integer"},
                },
            },
            git_diff,
        ),
        ToolSpec(
            "git_log",
            "Show up to 50 recent commits, optionally limited to one workspace path.",
            {
                **object_schema,
                "properties": {
                    "limit": {"type": "integer"},
                    "path": {"type": "string"},
                },
            },
            git_log,
        ),
    ]
