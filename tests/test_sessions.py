from __future__ import annotations

import json
from pathlib import Path
import shutil
import unittest
from uuid import uuid4

from forge_agent.history import ConversationHistory
from forge_agent.sessions import SessionError, SessionStore


class SessionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / "tmp" / "tests" / f"sessions-{uuid4().hex}"
        self.root.mkdir(parents=True)
        self.store = SessionStore(self.root)
        self.session_id = "20260829-150000-abcdef12"

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def history(self) -> ConversationHistory:
        history = ConversationHistory("system policy", "inspect the repository")
        history.add(
            {
                "role": "assistant",
                "content": "I will inspect it.",
                "reasoning_content": "Use the scoped list tool.",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "list_files", "arguments": "{}"},
                    }
                ],
            }
        )
        history.add(
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "name": "list_files",
                "content": '{"ok":true,"files":[]}',
            }
        )
        history.add({"role": "assistant", "content": "Inspection complete."})
        return history

    def test_round_trip_preserves_protocol_and_reasoning(self) -> None:
        path = self.store.save(
            self.session_id,
            self.history(),
            provider="deepseek",
            model="deepseek-v4-flash",
            audit_path="audit.jsonl",
        )
        loaded = self.store.load(self.session_id, max_chars=12_000)
        self.assertEqual(loaded.session_id, self.session_id)
        self.assertEqual(loaded.history.max_chars, 12_000)
        self.assertEqual(
            loaded.history.tail[0]["reasoning_content"],
            "Use the scoped list tool.",
        )
        self.assertEqual(loaded.history.tail[1]["tool_call_id"], "call_1")
        self.assertFalse(any(path.parent.glob("*.tmp")))

    def test_lists_resolves_prefix_and_latest(self) -> None:
        self.store.save(
            self.session_id,
            self.history(),
            provider="deepseek",
            model="deepseek-v4-flash",
            audit_path="audit.jsonl",
        )
        summaries = self.store.list()
        self.assertEqual(summaries[0].session_id, self.session_id)
        self.assertEqual(summaries[0].message_count, 5)
        self.assertEqual(summaries[0].task, "inspect the repository")
        self.assertEqual(self.store.resolve_id("20260829-150000"), self.session_id)
        self.assertEqual(self.store.resolve_id("1"), self.session_id)
        self.assertEqual(self.store.resolve_id("latest"), self.session_id)

    def test_rejects_path_traversal_and_wrong_workspace(self) -> None:
        with self.assertRaisesRegex(SessionError, "invalid characters"):
            self.store.load("../outside", max_chars=90_000)

        self.store.save(
            self.session_id,
            self.history(),
            provider="deepseek",
            model="deepseek-v4-flash",
            audit_path="audit.jsonl",
        )
        path = self.store.directory / f"{self.session_id}.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["workspace"] = "D:\\another-workspace"
        path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaisesRegex(SessionError, "different workspace"):
            self.store.load(self.session_id, max_chars=90_000)

    def test_corrupt_session_is_skipped_when_listing(self) -> None:
        (self.store.directory / "20260829-150001-fedcba98.json").write_text(
            "not-json", encoding="utf-8"
        )
        self.assertEqual(self.store.list(), [])


if __name__ == "__main__":
    unittest.main()
