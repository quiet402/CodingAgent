"""Dependency-free terminal presentation."""

from __future__ import annotations

import json
import re
import sys
from typing import Any

from .tools import ToolResult, ToolSpec


_SECRET_KEY = re.compile(r"(api.?key|authorization|password|secret|token)", re.I)
_SECRET_VALUE = re.compile(r"\b(sk-[A-Za-z0-9_-]{8,}|Bearer\s+\S+)", re.I)


def _redact_confirmation(value: Any, key: str = "") -> Any:
    if _SECRET_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {item_key: _redact_confirmation(item, item_key) for item_key, item in value.items()}
    if isinstance(value, list):
        return [_redact_confirmation(item) for item in value]
    if isinstance(value, str):
        redacted = _SECRET_VALUE.sub("[REDACTED]", value)
        if key == "content" and len(redacted) > 240:
            return redacted[:237] + "..."
        return redacted
    return value


class ConsoleUI:
    COLORS = {
        "cyan": "\033[36m",
        "green": "\033[32m",
        "yellow": "\033[33m",
        "red": "\033[31m",
        "bold": "\033[1m",
        "reset": "\033[0m",
    }

    def __init__(
        self,
        stream: Any = None,
        *,
        confirm_actions: bool = False,
        approve_all: bool = False,
    ) -> None:
        self.stream = stream or sys.stdout
        self.color = bool(getattr(self.stream, "isatty", lambda: False)())
        self._streaming = False
        self.confirm_actions = confirm_actions
        self._approve_all = approve_all

    def _paint(self, text: str, color: str) -> str:
        if not self.color:
            return text
        return f"{self.COLORS[color]}{text}{self.COLORS['reset']}"

    def print(self, text: str = "") -> None:
        print(text, file=self.stream, flush=True)

    def banner(
        self,
        provider: str,
        model: str,
        workspace: str,
        safe_mode: bool,
        stream: bool = True,
    ) -> None:
        self.print(self._paint("[ForgeAgent]", "bold"))
        self.print(f"  provider   {provider}")
        self.print(f"  model      {model}")
        self.print(f"  workspace  {workspace}")
        self.print(f"  safety     {'on' if safe_mode else 'OFF'}")
        self.print(f"  streaming  {'on' if stream else 'off'}\n")

    def step(self, number: int, max_steps: int) -> None:
        self.print(self._paint(f"[{number}/{max_steps}] thinking", "cyan"))

    def assistant_note(self, text: str) -> None:
        if text.strip():
            self.print(text.strip())

    def stream_chunk(self, text: str) -> None:
        if not text:
            return
        if not self._streaming:
            print(self._paint("  model  ", "cyan"), end="", file=self.stream, flush=True)
            self._streaming = True
        print(text, end="", file=self.stream, flush=True)

    def end_stream(self) -> bool:
        was_streaming = self._streaming
        if was_streaming:
            print(file=self.stream, flush=True)
        self._streaming = False
        return was_streaming

    def tool_start(self, name: str, arguments: str) -> None:
        try:
            compact = json.dumps(
                _redact_confirmation(json.loads(arguments)), ensure_ascii=False
            )
        except json.JSONDecodeError:
            compact = _SECRET_VALUE.sub("[REDACTED]", arguments)
        if len(compact) > 240:
            compact = compact[:237] + "..."
        self.print(self._paint(f"  -> {name}", "yellow") + f" {compact}")

    def confirm_tool(self, spec: ToolSpec, arguments: dict[str, Any]) -> bool:
        """Ask for approval before a tool marked as high risk is executed."""
        if not self.confirm_actions or self._approve_all:
            return True
        compact = json.dumps(
            _redact_confirmation(arguments), ensure_ascii=False, separators=(",", ":")
        )
        if len(compact) > 600:
            compact = compact[:597] + "..."
        self.print(self._paint("  [confirmation required]", "yellow"))
        self.print(f"  {spec.name}: {compact}")
        self.print("  Allow this action? [y/N/a=allow all]")
        try:
            answer = input().strip().casefold()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer in {"a", "all"}:
            self._approve_all = True
            self.print("  [approved; future actions auto-approved]")
            return True
        approved = answer in {"y", "yes"}
        self.print("  [approved]" if approved else "  [denied]")
        return approved

    def tool_end(self, result: ToolResult) -> None:
        mark, color = ("[ok]", "green") if result.ok else ("[error]", "red")
        first_line = result.message.splitlines()[0] if result.message else ""
        self.print(self._paint(f"  {mark}", color) + f" {first_line[:180]}")

    def final(self, text: str, *, already_streamed: bool = False) -> None:
        if already_streamed:
            self.print(self._paint("[Turn complete]", "bold"))
        else:
            self.print("\n" + self._paint("[Result]", "bold"))
            self.print(text.strip())

    def summary(self, *, steps: int, tool_calls: int, duration: float, stop_reason: str) -> None:
        self.print(
            f"\nsteps={steps}  tools={tool_calls}  time={duration:.1f}s  stop={stop_reason}"
        )
