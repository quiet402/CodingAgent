"""Append-only JSONL run traces with basic secret redaction."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any
from uuid import uuid4


SECRET_KEY = re.compile(r"(api.?key|authorization|password|secret|token)", re.I)
SECRET_VALUE = re.compile(r"\b(sk-[A-Za-z0-9_-]{8,}|Bearer\s+\S+)", re.I)


def _redact(value: Any, key: str = "") -> Any:
    if SECRET_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {item_key: _redact(item, item_key) for item_key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return SECRET_VALUE.sub("[REDACTED]", value)
    return value


class AuditLog:
    def __init__(self, workspace: Path, session_id: str | None = None) -> None:
        run_dir = workspace / ".forge" / "runs"
        run_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.session_id = session_id or f"{stamp}-{uuid4().hex[:8]}"
        self.path = run_dir / f"{self.session_id}.jsonl"

    def record(self, event: str, **data: Any) -> None:
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": self.session_id,
            "event": event,
            **_redact(data),
        }
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
