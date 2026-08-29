"""Small tool protocol and validation layer."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Callable


JsonObject = dict[str, Any]
ToolHandler = Callable[[JsonObject], "ToolResult"]


@dataclass(slots=True)
class ToolResult:
    ok: bool
    message: str
    metadata: JsonObject = field(default_factory=dict)

    @classmethod
    def success(cls, message: str, **metadata: Any) -> "ToolResult":
        return cls(True, message, metadata)

    @classmethod
    def failure(cls, message: str, **metadata: Any) -> "ToolResult":
        return cls(False, message, metadata)

    def model_content(self) -> str:
        payload = {"ok": self.ok, "message": self.message}
        if self.metadata:
            payload["metadata"] = self.metadata
        return json.dumps(payload, ensure_ascii=False)


@dataclass(slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: JsonObject
    handler: ToolHandler

    def api_schema(self) -> JsonObject:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self, specs: list[ToolSpec], output_limit: int = 30_000) -> None:
        self._specs = {spec.name: spec for spec in specs}
        if len(self._specs) != len(specs):
            raise ValueError("Tool names must be unique")
        self.output_limit = output_limit

    @property
    def schemas(self) -> list[JsonObject]:
        return [spec.api_schema() for spec in self._specs.values()]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._specs)

    def call(self, name: str, raw_arguments: str) -> ToolResult:
        spec = self._specs.get(name)
        if spec is None:
            return ToolResult.failure(
                f"Unknown tool: {name}", available_tools=list(self._specs)
            )
        try:
            arguments = json.loads(raw_arguments or "{}")
        except json.JSONDecodeError as exc:
            return ToolResult.failure(
                f"Arguments are not valid JSON: {exc.msg}", position=exc.pos
            )
        if not isinstance(arguments, dict):
            return ToolResult.failure("Tool arguments must be a JSON object")

        validation_error = self._validate(spec.parameters, arguments)
        if validation_error:
            return ToolResult.failure(validation_error)
        try:
            result = spec.handler(arguments)
        except Exception as exc:  # Tool faults become observations, not agent crashes.
            result = ToolResult.failure(f"{type(exc).__name__}: {exc}")
        result.message = self._truncate(result.message)
        return result

    def _truncate(self, text: str) -> str:
        if len(text) <= self.output_limit:
            return text
        kept = self.output_limit // 2
        removed = len(text) - (kept * 2)
        return (
            text[:kept]
            + f"\n... [tool output truncated: {removed} characters omitted] ...\n"
            + text[-kept:]
        )

    @staticmethod
    def _validate(schema: JsonObject, values: JsonObject) -> str | None:
        required = schema.get("required", [])
        missing = [key for key in required if key not in values]
        if missing:
            return f"Missing required argument(s): {', '.join(missing)}"

        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extras = sorted(set(values) - set(properties))
            if extras:
                return f"Unexpected argument(s): {', '.join(extras)}"

        expected_types = {
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
            "array": list,
            "object": dict,
        }
        for key, value in values.items():
            expected_name = properties.get(key, {}).get("type")
            expected = expected_types.get(expected_name)
            boolean_number = expected_name in {"integer", "number"} and isinstance(value, bool)
            if expected and (not isinstance(value, expected) or boolean_number):
                return f"Argument '{key}' must be {expected_name}"
        return None
