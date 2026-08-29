"""The model-tool-observation loop."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import time
from typing import Any

from .audit import AuditLog
from .config import AgentConfig
from .history import ConversationHistory
from .model import ModelClient, ModelError, ToolCall
from .prompts import SYSTEM_PROMPT
from .sessions import LoadedSession, SessionError, SessionStore, SessionSummary
from .tools import ToolRegistry, ToolResult
from .ui import ConsoleUI


@dataclass(slots=True)
class AgentResult:
    final_text: str
    stop_reason: str
    steps: int
    tool_calls: int
    tool_errors: int
    changed_files: list[str] = field(default_factory=list)
    audit_path: str = ""
    duration_seconds: float = 0.0


class AgentRunner:
    def __init__(
        self,
        client: ModelClient,
        tools: ToolRegistry,
        config: AgentConfig,
        *,
        ui: ConsoleUI | None = None,
        audit: AuditLog | None = None,
    ) -> None:
        self.client = client
        self.tools = tools
        self.config = config
        self.ui = ui or ConsoleUI()
        self.audit = audit or AuditLog(config.workspace)
        self.sessions = SessionStore(config.workspace)
        self.history: ConversationHistory | None = None

    def reset_session(self) -> None:
        self.history = None
        self.audit = AuditLog(self.config.workspace)

    def resume_session(self, session_id: str) -> LoadedSession:
        loaded = self.sessions.load(session_id, max_chars=self.config.context_chars)
        if loaded.provider != self.config.provider:
            raise SessionError(
                f"Session uses provider {loaded.provider!r}, but the current provider is "
                f"{self.config.provider!r}"
            )
        self.history = loaded.history
        self.audit = AuditLog(self.config.workspace, loaded.session_id)
        self.audit.record(
            "session_resumed",
            saved_model=loaded.model,
            current_model=self.config.model,
            message_count=loaded.history.message_count,
        )
        self._save_session()
        return loaded

    def list_sessions(self, limit: int = 20) -> list[SessionSummary]:
        return self.sessions.list(limit)

    @property
    def session_path(self) -> str:
        return str(self.sessions.directory / f"{self.audit.session_id}.json")

    def _save_session(self) -> bool:
        if self.history is None:
            return False
        try:
            self.sessions.save(
                self.audit.session_id,
                self.history,
                provider=self.config.provider,
                model=self.config.model,
                audit_path=str(self.audit.path),
            )
            return True
        except SessionError as exc:
            self.audit.record("session_save_error", error=str(exc))
            self.ui.print(f"[warning] Session could not be saved: {exc}")
            return False

    def run(self, task: str, *, continue_session: bool = False) -> AgentResult:
        started = time.monotonic()
        if continue_session and self.history is not None:
            self.history.add_user(task)
        else:
            self.history = ConversationHistory(
                SYSTEM_PROMPT, task, self.config.context_chars
            )
        history = self.history
        self._save_session()
        tool_count = 0
        tool_errors = 0
        consecutive_errors = 0
        changed_files: set[str] = set()
        last_fingerprint = ""
        repeated = 0
        streamed = False
        self.audit.record(
            "run_started",
            task=task,
            model=self.config.model,
            workspace=str(self.config.workspace),
            safe_mode=self.config.safe_mode,
        )

        final_text = ""
        stop_reason = "max_steps"
        completed_steps = 0
        for step in range(1, self.config.max_steps + 1):
            completed_steps = step
            self.ui.step(step, self.config.max_steps)
            messages = history.request_messages()
            self.audit.record(
                "model_request",
                step=step,
                message_count=len(messages),
                approximate_chars=sum(len(json.dumps(m, ensure_ascii=False)) for m in messages),
            )
            try:
                turn = self.client.complete(
                    messages,
                    self.tools.schemas,
                    on_text=self.ui.stream_chunk if self.config.stream else None,
                )
            except ModelError as exc:
                self.ui.end_stream()
                streamed = False
                final_text = f"Model request failed: {exc}"
                stop_reason = "model_error"
                self.audit.record("model_error", step=step, error=str(exc))
                break

            streamed = self.ui.end_stream()
            history.add(turn.as_message())
            self.audit.record(
                "model_response",
                step=step,
                content=turn.content,
                tool_calls=[call.as_message_value() for call in turn.tool_calls],
            )
            if turn.tool_calls and not streamed:
                self.ui.assistant_note(turn.content)
            if not turn.tool_calls:
                if turn.content.strip():
                    final_text = turn.content
                    stop_reason = "completed"
                else:
                    final_text = "Model returned neither text nor tool calls."
                    stop_reason = "empty_response"
                self._save_session()
                break

            for call in turn.tool_calls:
                tool_count += 1
                fingerprint = self._fingerprint(call)
                repeated = repeated + 1 if fingerprint == last_fingerprint else 1
                last_fingerprint = fingerprint
                self.ui.tool_start(call.name, call.arguments)
                if repeated >= 3:
                    result = ToolResult.failure(
                        "Loop guard blocked a third consecutive identical tool call. Change the approach."
                    )
                else:
                    result = self.tools.call(call.name, call.arguments)
                if result.ok:
                    consecutive_errors = 0
                    if result.metadata.get("changed"):
                        changed_paths = result.metadata.get("changed_paths")
                        if isinstance(changed_paths, list):
                            changed_files.update(str(path) for path in changed_paths)
                        elif result.metadata.get("path"):
                            changed_files.add(str(result.metadata["path"]))
                else:
                    tool_errors += 1
                    consecutive_errors += 1
                history.add(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": call.name,
                        "content": result.model_content(),
                    }
                )
                self.audit.record(
                    "tool_result",
                    step=step,
                    tool_call_id=call.id,
                    tool=call.name,
                    ok=result.ok,
                    message=result.message,
                    metadata=result.metadata,
                )
                self.ui.tool_end(result)
            self._save_session()
            if consecutive_errors >= 6:
                final_text = "Stopped after six consecutive tool errors; inspect the audit trace."
                stop_reason = "tool_error_budget"
                break
        else:
            final_text = "Maximum step count reached before the model produced a final response."

        duration = time.monotonic() - started
        self.audit.record(
            "run_finished",
            stop_reason=stop_reason,
            steps=completed_steps,
            tool_calls=tool_count,
            tool_errors=tool_errors,
            changed_files=sorted(changed_files),
            duration_seconds=round(duration, 3),
        )
        self._save_session()
        self.ui.final(final_text, already_streamed=streamed and stop_reason == "completed")
        self.ui.summary(
            steps=completed_steps,
            tool_calls=tool_count,
            duration=duration,
            stop_reason=stop_reason,
        )
        return AgentResult(
            final_text=final_text,
            stop_reason=stop_reason,
            steps=completed_steps,
            tool_calls=tool_count,
            tool_errors=tool_errors,
            changed_files=sorted(changed_files),
            audit_path=str(self.audit.path),
            duration_seconds=duration,
        )

    @staticmethod
    def _fingerprint(call: ToolCall) -> str:
        try:
            normalized = json.dumps(json.loads(call.arguments), sort_keys=True)
        except json.JSONDecodeError:
            normalized = call.arguments
        return hashlib.sha256(f"{call.name}\0{normalized}".encode()).hexdigest()
