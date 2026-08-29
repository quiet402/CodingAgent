"""Atomic local persistence for resumable conversation sessions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from .history import ConversationHistory


SESSION_ID = re.compile(r"^\d{8}-\d{6}-[a-f0-9]{8}$")
SESSION_PREFIX = re.compile(r"^[A-Za-z0-9-]+$")


class SessionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SessionSummary:
    session_id: str
    updated_at: str
    message_count: int
    task: str
    provider: str
    model: str


@dataclass(frozen=True, slots=True)
class LoadedSession:
    session_id: str
    history: ConversationHistory
    provider: str
    model: str
    audit_path: str


class SessionStore:
    FORMAT_VERSION = 1

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.directory = self.workspace / ".forge" / "sessions"
        self.directory.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        session_id: str,
        history: ConversationHistory,
        *,
        provider: str,
        model: str,
        audit_path: str,
    ) -> Path:
        self._validate_id(session_id)
        path = self._path(session_id)
        now = datetime.now(timezone.utc).isoformat()
        created_at = now
        if path.is_file():
            try:
                current = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(current.get("created_at"), str):
                    created_at = current["created_at"]
            except (OSError, json.JSONDecodeError, AttributeError):
                pass
        document = {
            "format_version": self.FORMAT_VERSION,
            "session_id": session_id,
            "created_at": created_at,
            "updated_at": now,
            "workspace": str(self.workspace),
            "provider": provider,
            "model": model,
            "audit_path": audit_path,
            "history": history.as_dict(),
        }
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(document, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(path)
        except OSError as exc:
            raise SessionError(f"Cannot save session {session_id}: {exc}") from exc
        finally:
            temporary.unlink(missing_ok=True)
        return path

    def load(self, requested_id: str, *, max_chars: int) -> LoadedSession:
        session_id = self.resolve_id(requested_id)
        path = self._path(session_id)
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise SessionError(f"Session not found: {requested_id}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise SessionError(f"Cannot read session {session_id}: {exc}") from exc
        self._validate_document(document, session_id)
        try:
            history = ConversationHistory.from_dict(
                document["history"], max_chars=max_chars
            )
        except ValueError as exc:
            raise SessionError(f"Invalid session {session_id}: {exc}") from exc
        return LoadedSession(
            session_id=session_id,
            history=history,
            provider=str(document.get("provider", "unknown")),
            model=str(document.get("model", "unknown")),
            audit_path=str(document.get("audit_path", "")),
        )

    def list(self, limit: int = 20) -> list[SessionSummary]:
        summaries: list[SessionSummary] = []
        for path in self.directory.glob("*.json"):
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
                session_id = str(document.get("session_id", ""))
                self._validate_document(document, session_id)
                history = ConversationHistory.from_dict(document["history"])
            except (OSError, json.JSONDecodeError, ValueError, SessionError):
                continue
            task = str(history.user.get("content", "")).replace("\n", " ").strip()
            summaries.append(
                SessionSummary(
                    session_id=session_id,
                    updated_at=str(document.get("updated_at", "")),
                    message_count=history.message_count,
                    task=task[:80],
                    provider=str(document.get("provider", "unknown")),
                    model=str(document.get("model", "unknown")),
                )
            )
        summaries.sort(key=lambda item: item.updated_at, reverse=True)
        return summaries[: max(0, limit)]

    def resolve_id(self, requested_id: str) -> str:
        requested = requested_id.strip()
        if requested.casefold() == "latest":
            sessions = self.list(limit=1)
            if not sessions:
                raise SessionError("No saved sessions found")
            return sessions[0].session_id
        if requested.isdecimal():
            index = int(requested) - 1
            sessions = self.list()
            if index < 0 or index >= len(sessions):
                raise SessionError(f"Session number is out of range: {requested}")
            return sessions[index].session_id
        if not requested or not SESSION_PREFIX.fullmatch(requested):
            raise SessionError("Session ID contains invalid characters")
        if SESSION_ID.fullmatch(requested) and self._path(requested).is_file():
            return requested
        matches = [
            path.stem
            for path in self.directory.glob(f"{requested}*.json")
            if SESSION_ID.fullmatch(path.stem)
        ]
        if not matches:
            raise SessionError(f"Session not found: {requested}")
        if len(matches) > 1:
            raise SessionError(f"Session prefix is ambiguous: {requested}")
        return matches[0]

    def _path(self, session_id: str) -> Path:
        return self.directory / f"{session_id}.json"

    @staticmethod
    def _validate_id(session_id: str) -> None:
        if not SESSION_ID.fullmatch(session_id):
            raise SessionError(f"Invalid session ID: {session_id}")

    def _validate_document(self, document: Any, session_id: str) -> None:
        if not isinstance(document, dict):
            raise SessionError("Session document must be a JSON object")
        self._validate_id(session_id)
        if document.get("session_id") != session_id:
            raise SessionError("Session ID does not match its filename")
        if document.get("format_version") != self.FORMAT_VERSION:
            raise SessionError("Unsupported session format version")
        if document.get("workspace") != str(self.workspace):
            raise SessionError("Session belongs to a different workspace")
        if not isinstance(document.get("history"), dict):
            raise SessionError("Session has no valid history object")
