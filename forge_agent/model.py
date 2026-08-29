"""Minimal OpenAI-compatible chat client implemented with the standard library."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import time
from typing import Any, Callable, Iterable, Protocol
from urllib import error, request


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: str

    def as_message_value(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments},
        }


@dataclass(slots=True)
class AssistantTurn:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    reasoning_content: str = ""

    def as_message(self) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": self.content or None}
        if self.tool_calls:
            message["tool_calls"] = [call.as_message_value() for call in self.tool_calls]
        if self.reasoning_content:
            message["reasoning_content"] = self.reasoning_content
        return message


class ModelClient(Protocol):
    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        on_text: Callable[[str], None] | None = None,
    ) -> AssistantTurn: ...


class ModelError(RuntimeError):
    pass


class OpenAICompatibleClient:
    """Use the documented chat-completions wire format without an SDK dependency."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        retries: int = 3,
        timeout: int = 90,
        thinking: str | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        base = base_url.rstrip("/")
        self.endpoint = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
        self.api_key = api_key
        self.model = model
        self.retries = retries
        self.timeout = timeout
        self.thinking = thinking
        self.reasoning_effort = reasoning_effort

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        on_text: Callable[[str], None] | None = None,
    ) -> AssistantTurn:
        payload: dict[str, Any] = {"model": self.model, "messages": messages}
        if self.thinking is not None:
            payload["thinking"] = {"type": self.thinking}
        if self.reasoning_effort is not None:
            payload["reasoning_effort"] = self.reasoning_effort
        if tools:
            payload.update({"tools": tools, "tool_choice": "auto"})
        if on_text is not None:
            payload["stream"] = True

        last_error = "unknown API error"
        attempts_used = 0
        for attempt in range(self.retries + 1):
            attempts_used = attempt + 1
            emitted = False

            def emit(fragment: str) -> None:
                nonlocal emitted
                emitted = True
                on_text(fragment)  # type: ignore[misc]

            try:
                response = self._open(payload)
                with response:
                    if on_text is None:
                        document = json.loads(response.read().decode("utf-8"))
                        return self._parse(document)
                    return self._parse_stream(response, emit)
            except error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")[:2000]
                last_error = f"HTTP {exc.code}: {body}"
                retryable = exc.code == 429 or 500 <= exc.code < 600
                if emitted or not retryable or attempt >= self.retries:
                    break
            except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if emitted or attempt >= self.retries:
                    break
            time.sleep(min(2**attempt, 8))
        raise ModelError(f"Model request failed after {attempts_used} attempt(s): {last_error}")

    def _open(self, payload: dict[str, Any]):
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        accept = "text/event-stream" if payload.get("stream") else "application/json"
        headers = {"Content-Type": "application/json", "Accept": accept}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = request.Request(self.endpoint, data=encoded, headers=headers, method="POST")
        return request.urlopen(req, timeout=self.timeout)

    @classmethod
    def _parse_stream(
        cls,
        lines: Iterable[bytes],
        on_text: Callable[[str], None],
    ) -> AssistantTurn:
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        for raw_line in lines:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line or line.startswith(":"):
                continue
            if line.startswith("data:"):
                data = line[5:].strip()
            elif line.startswith("{"):
                document = json.loads(line)
                choices = document.get("choices") or []
                if choices and choices[0].get("message") is not None:
                    turn = cls._parse(document)
                    if turn.content:
                        on_text(turn.content)
                    return turn
                data = line
            else:
                continue
            if data == "[DONE]":
                break
            document = json.loads(data)
            if document.get("error"):
                raise ModelError(f"Streaming API error: {document['error']}")
            choices = document.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            fragment = cls._content_text(delta.get("content"))
            if fragment:
                content_parts.append(fragment)
                on_text(fragment)
            reasoning_fragment = cls._content_text(delta.get("reasoning_content"))
            if reasoning_fragment:
                reasoning_parts.append(reasoning_fragment)
            for value in delta.get("tool_calls") or []:
                index = int(value.get("index", 0))
                current = calls.setdefault(
                    index, {"id": "", "name": "", "arguments": ""}
                )
                current["id"] += value.get("id") or ""
                function = value.get("function") or {}
                current["name"] += function.get("name") or ""
                arguments = function.get("arguments") or ""
                if not isinstance(arguments, str):
                    arguments = json.dumps(arguments, ensure_ascii=False)
                current["arguments"] += arguments
        tool_calls = [
            ToolCall(
                id=value["id"] or f"call_{index}",
                name=value["name"],
                arguments=value["arguments"] or "{}",
            )
            for index, value in sorted(calls.items())
        ]
        return AssistantTurn("".join(content_parts), tool_calls, "".join(reasoning_parts))

    @staticmethod
    def _content_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                item.get("text", "") for item in content if isinstance(item, dict)
            )
        return ""

    @staticmethod
    def _parse(document: dict[str, Any]) -> AssistantTurn:
        try:
            message = document["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelError("API response has no choices[0].message") from exc

        content = OpenAICompatibleClient._content_text(message.get("content"))
        reasoning_content = OpenAICompatibleClient._content_text(
            message.get("reasoning_content")
        )
        calls: list[ToolCall] = []
        for index, value in enumerate(message.get("tool_calls") or []):
            function = value.get("function") or {}
            arguments = function.get("arguments") or "{}"
            if not isinstance(arguments, str):
                arguments = json.dumps(arguments, ensure_ascii=False)
            calls.append(
                ToolCall(
                    id=value.get("id") or f"call_{index}",
                    name=function.get("name", ""),
                    arguments=arguments,
                )
            )
        legacy = message.get("function_call")
        if legacy and not calls:
            arguments = legacy.get("arguments") or "{}"
            if not isinstance(arguments, str):
                arguments = json.dumps(arguments, ensure_ascii=False)
            calls.append(
                ToolCall(
                    id="legacy_function_call",
                    name=legacy.get("name", ""),
                    arguments=arguments,
                )
            )
        return AssistantTurn(str(content), calls, str(reasoning_content))
