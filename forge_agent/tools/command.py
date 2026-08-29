"""Local command execution with an explainable default safety policy."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
from typing import Any

from .core import ToolResult, ToolSpec
from .filesystem import Workspace


DEFAULT_ALLOWED = {
    "cargo", "dotnet", "git", "go", "gradle", "gradlew", "java", "javac",
    "mvn", "mvnw", "node", "npm", "npm.cmd", "npx", "npx.cmd", "py",
    "pytest", "python", "python3", "rg", "ruff", "mypy",
}


@dataclass(slots=True)
class CommandPolicy:
    safe_mode: bool = True
    allowed_programs: set[str] = field(default_factory=lambda: set(DEFAULT_ALLOWED))

    def check(self, command: str, workspace: Workspace) -> tuple[bool, str, list[str]]:
        if not command.strip():
            return False, "Command is empty", []
        if not self.safe_mode:
            try:
                return True, "unsafe mode enabled", shlex.split(command, posix=os.name != "nt")
            except ValueError as exc:
                return False, f"Cannot parse command: {exc}", []

        if re.search(r"[;&|<>\r\n`]", command):
            return False, "Shell operators and redirection are blocked in safe mode", []
        try:
            tokens = shlex.split(command, posix=os.name != "nt")
        except ValueError as exc:
            return False, f"Cannot parse command: {exc}", []
        tokens = [token.strip('"') for token in tokens]
        if not tokens:
            return False, "Command is empty", []

        program = Path(tokens[0]).name.casefold()
        if program not in self.allowed_programs:
            return False, f"Program '{program}' is not allowed in safe mode", []
        if program in {"python", "python3", "py", "node"} and any(
            token in {"-c", "-e", "--eval"} for token in tokens[1:]
        ):
            return False, "Inline interpreter programs are blocked in safe mode", []
        if any(".." in Path(token).parts for token in tokens[1:] if token):
            return False, "Parent-directory traversal is blocked in command arguments", []
        for token in tokens[1:]:
            if token.startswith("-"):
                continue
            try:
                workspace.resolve(token)
            except PermissionError as exc:
                return False, f"Command argument path is blocked: {token} ({exc})", []
        return True, "allowed", tokens


def build_command_tool(
    workspace: Workspace,
    policy: CommandPolicy,
    default_timeout: int = 30,
) -> ToolSpec:
    def run_command(args: dict[str, Any]) -> ToolResult:
        allowed, reason, tokens = policy.check(args["command"], workspace)
        if not allowed:
            return ToolResult.failure(
                f"Command blocked by safety policy: {reason}", safe_mode=policy.safe_mode
            )
        if Path(tokens[0]).name.casefold() in {"python", "python3", "py"}:
            tokens[0] = sys.executable
        cwd = workspace.resolve(args.get("working_directory", "."), must_exist=True)
        if not cwd.is_dir():
            return ToolResult.failure("working_directory must be a directory")
        timeout = args.get("timeout_seconds", default_timeout)
        if not 1 <= timeout <= 300:
            return ToolResult.failure("timeout_seconds must be between 1 and 300")
        try:
            completed = subprocess.run(
                tokens,
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            partial = "".join(
                part for part in (exc.stdout or "", exc.stderr or "") if isinstance(part, str)
            )
            return ToolResult.failure(
                f"Command timed out after {timeout}s\n{partial}", timed_out=True
            )
        except OSError as exc:
            return ToolResult.failure(f"Could not start command: {exc}")

        combined = completed.stdout
        if completed.stderr:
            combined += ("\n" if combined else "") + "[stderr]\n" + completed.stderr
        combined = combined.rstrip() or "(no output)"
        factory = ToolResult.success if completed.returncode == 0 else ToolResult.failure
        return factory(
            combined,
            exit_code=completed.returncode,
            working_directory=workspace.display(cwd),
        )

    return ToolSpec(
        "run_command",
        "Run one local development command in the workspace and return stdout, stderr, and exit code.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "command": {"type": "string", "description": "One command, without shell operators"},
                "working_directory": {"type": "string"},
                "timeout_seconds": {"type": "integer", "description": "1 to 300"},
            },
            "required": ["command"],
        },
        run_command,
        requires_confirmation=True,
    )
