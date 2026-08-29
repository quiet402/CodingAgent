"""Conversation storage with tool-call-aware deterministic compaction."""

from __future__ import annotations

import json
from typing import Any


def _size(message: dict[str, Any]) -> int:
    return len(json.dumps(message, ensure_ascii=False, default=str))


class ConversationHistory:
    """Keep the task pinned and remove only complete assistant/tool blocks."""

    def __init__(self, system_prompt: str, task: str, max_chars: int = 90_000) -> None:
        self.system = {"role": "system", "content": system_prompt}
        self.user = {"role": "user", "content": task}
        self.tail: list[dict[str, Any]] = []
        self.max_chars = max_chars

    def add(self, message: dict[str, Any]) -> None:
        self.tail.append(message)

    def add_user(self, content: str) -> None:
        self.tail.append({"role": "user", "content": content})

    def as_dict(self) -> dict[str, Any]:
        """Return the complete, uncompacted protocol history for persistence."""
        return {
            "system": dict(self.system),
            "user": dict(self.user),
            "tail": [dict(message) for message in self.tail],
            "max_chars": self.max_chars,
        }

    @classmethod
    def from_dict(
        cls,
        value: dict[str, Any],
        *,
        max_chars: int | None = None,
    ) -> "ConversationHistory":
        """Validate and restore a persisted protocol history."""
        system = value.get("system")
        user = value.get("user")
        tail = value.get("tail")
        if not isinstance(system, dict) or system.get("role") != "system":
            raise ValueError("Persisted history has no valid system message")
        if not isinstance(user, dict) or user.get("role") != "user":
            raise ValueError("Persisted history has no valid initial user message")
        if not isinstance(tail, list) or not all(isinstance(item, dict) for item in tail):
            raise ValueError("Persisted history tail must be a list of messages")
        allowed_roles = {"user", "assistant", "tool", "system"}
        if any(message.get("role") not in allowed_roles for message in tail):
            raise ValueError("Persisted history contains an unsupported message role")

        stored_max = value.get("max_chars", 90_000)
        if not isinstance(stored_max, int):
            raise ValueError("Persisted history max_chars must be an integer")
        history = cls(
            str(system.get("content", "")),
            str(user.get("content", "")),
            max_chars if max_chars is not None else stored_max,
        )
        history.system = dict(system)
        history.user = dict(user)
        history.tail = [dict(message) for message in tail]
        return history

    @property
    def message_count(self) -> int:
        return 2 + len(self.tail)

    def request_messages(self) -> list[dict[str, Any]]:
        complete = [self.system, self.user, *self.tail]
        if sum(_size(item) for item in complete) <= self.max_chars:
            return complete

        blocks = self._blocks()
        base_size = _size(self.system) + _size(self.user) + 2500
        kept: list[list[dict[str, Any]]] = []
        used = base_size
        for block in reversed(blocks):
            block_size = sum(_size(item) for item in block)
            if kept and used + block_size > self.max_chars:
                break
            if not kept and used + block_size > self.max_chars:
                block = self._shrink(block)
                block_size = sum(_size(item) for item in block)
            kept.append(block)
            used += block_size
        kept.reverse()
        omitted_count = len(blocks) - len(kept)
        output = [self.system]
        if omitted_count:
            summary = self._summarize(blocks[:omitted_count])
            output.append({"role": "system", "content": summary})
        output.append(self.user)
        output.extend(message for block in kept for message in block)
        return output

    def _blocks(self) -> list[list[dict[str, Any]]]:
        blocks: list[list[dict[str, Any]]] = []
        for message in self.tail:
            role = message.get("role")
            if role in {"assistant", "user"} or not blocks:
                blocks.append([message])
            else:
                blocks[-1].append(message)
        return blocks

    @staticmethod
    def _shrink(block: list[dict[str, Any]]) -> list[dict[str, Any]]:
        shrunk: list[dict[str, Any]] = []
        for message in block:
            copy = dict(message)
            content = copy.get("content")
            if message.get("role") == "tool" and isinstance(content, str) and len(content) > 4000:
                copy["content"] = content[:2000] + "\n...[compacted]...\n" + content[-2000:]
            shrunk.append(copy)
        return shrunk

    @staticmethod
    def _summarize(blocks: list[list[dict[str, Any]]]) -> str:
        rows = ["Earlier activity was deterministically compacted:"]
        for block in blocks[-20:]:
            assistant = block[0]
            text = (assistant.get("content") or "").replace("\n", " ")[:240]
            role = assistant.get("role", "message").title()
            names = [
                call.get("function", {}).get("name", "unknown")
                for call in assistant.get("tool_calls", [])
            ]
            if text:
                rows.append(f"- {role}: {text}")
            if names:
                rows.append(f"- Called: {', '.join(names)}")
            for tool_message in block[1:]:
                content = str(tool_message.get("content", ""))
                rows.append(f"- Tool observation: {content[:350].replace(chr(10), ' ')}")
        if len(blocks) > 20:
            rows.insert(1, f"- {len(blocks) - 20} older block(s) omitted from this summary.")
        return "\n".join(rows)
