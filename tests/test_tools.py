from __future__ import annotations

import json
import hashlib
from pathlib import Path
import shutil
import sys
import unittest
from uuid import uuid4

from forge_agent.tools import (
    CommandPolicy,
    ToolRegistry,
    ToolResult,
    ToolSpec,
    Workspace,
    build_command_tool,
    build_filesystem_tools,
)


class WorkspaceToolTests(unittest.TestCase):
    def setUp(self) -> None:
        test_temp = Path.cwd() / "tmp" / "tests"
        test_temp.mkdir(parents=True, exist_ok=True)
        self.root = test_temp / f"case-{uuid4().hex}"
        self.root.mkdir()
        self.workspace = Workspace(self.root)
        specs = build_filesystem_tools(self.workspace)
        self.registry = ToolRegistry(specs, output_limit=20_000)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def call(self, name: str, **arguments: object):
        return self.registry.call(name, json.dumps(arguments))

    def test_write_read_replace_and_search(self) -> None:
        created = self.call("write_file", path="src/hello.py", content="print('old')\n")
        self.assertTrue(created.ok, created.message)
        self.assertEqual(created.metadata["path"], "src/hello.py")

        read = self.call("read_file", path="src/hello.py")
        self.assertTrue(read.ok)
        self.assertIn("1 | print('old')", read.message)

        replaced = self.call(
            "replace_in_file",
            path="src/hello.py",
            old_text="old",
            new_text="new",
        )
        self.assertTrue(replaced.ok, replaced.message)
        self.assertEqual((self.root / "src" / "hello.py").read_text(), "print('new')\n")

        search = self.call("search_text", query="NEW", path="src", glob="*.py")
        self.assertTrue(search.ok)
        self.assertIn("src/hello.py:1", search.message)

    def test_existing_write_requires_explicit_overwrite(self) -> None:
        (self.root / "file.txt").write_text("keep", encoding="utf-8")
        result = self.call("write_file", path="file.txt", content="replace")
        self.assertFalse(result.ok)
        self.assertEqual((self.root / "file.txt").read_text(), "keep")

    def test_exact_replace_is_transactional(self) -> None:
        path = self.root / "file.txt"
        path.write_text("same same", encoding="utf-8")
        result = self.call(
            "replace_in_file",
            path="file.txt",
            old_text="same",
            new_text="changed",
            expected_count=1,
        )
        self.assertFalse(result.ok)
        self.assertEqual(path.read_text(), "same same")

    def test_path_escape_is_rejected(self) -> None:
        outside = self.root.parent / f"{self.root.name}-outside.txt"
        result = self.call("write_file", path=str(outside), content="bad")
        self.assertFalse(result.ok)
        self.assertIn("escapes workspace", result.message)
        self.assertFalse(outside.exists())

    def test_schema_validation_handles_bad_json_and_extra_args(self) -> None:
        malformed = self.registry.call("read_file", "{bad")
        self.assertFalse(malformed.ok)
        extra = self.call("read_file", path="missing", surprise=True)
        self.assertFalse(extra.ok)
        self.assertIn("Unexpected", extra.message)

    def test_batch_read_info_and_directory_creation(self) -> None:
        directory = self.call("make_directory", path="src/package")
        self.assertTrue(directory.ok, directory.message)
        self.assertTrue((self.root / "src" / "package").is_dir())
        (self.root / "src" / "a.py").write_text("a = 1\n", encoding="utf-8")
        (self.root / "src" / "b.py").write_text("b = 2\n", encoding="utf-8")

        batch = self.call("read_files", paths=["src/a.py", "src/b.py"])
        self.assertTrue(batch.ok, batch.message)
        self.assertIn("### src/a.py", batch.message)
        self.assertIn("### src/b.py", batch.message)

        info = self.call("file_info", path="src/a.py")
        self.assertTrue(info.ok)
        self.assertEqual(info.metadata["type"], "file")
        self.assertEqual(
            info.metadata["sha256"],
            hashlib.sha256((self.root / "src" / "a.py").read_bytes()).hexdigest(),
        )

    def test_apply_edits_is_transactional(self) -> None:
        path = self.root / "module.py"
        path.write_text("alpha = 1\nbeta = 2\n", encoding="utf-8")
        failed = self.call(
            "apply_edits",
            path="module.py",
            edits=[
                {"old_text": "alpha = 1", "new_text": "alpha = 10"},
                {"old_text": "missing", "new_text": "value"},
            ],
        )
        self.assertFalse(failed.ok)
        self.assertEqual(path.read_text(encoding="utf-8"), "alpha = 1\nbeta = 2\n")

        applied = self.call(
            "apply_edits",
            path="module.py",
            edits=[
                {"old_text": "alpha = 1", "new_text": "alpha = 10"},
                {"old_text": "beta = 2", "new_text": "beta = 20"},
            ],
        )
        self.assertTrue(applied.ok, applied.message)
        self.assertEqual(path.read_text(encoding="utf-8"), "alpha = 10\nbeta = 20\n")

    def test_move_and_hash_confirmed_delete(self) -> None:
        source = self.root / "old.txt"
        source.write_text("important", encoding="utf-8")
        moved = self.call("move_file", source="old.txt", destination="archive/new.txt")
        self.assertTrue(moved.ok, moved.message)
        destination = self.root / "archive" / "new.txt"
        self.assertFalse(source.exists())
        self.assertTrue(destination.is_file())

        wrong = self.call("delete_file", path="archive/new.txt", expected_sha256="0" * 64)
        self.assertFalse(wrong.ok)
        self.assertTrue(destination.exists())
        digest = hashlib.sha256(b"important").hexdigest()
        deleted = self.call(
            "delete_file", path="archive/new.txt", expected_sha256=digest
        )
        self.assertTrue(deleted.ok, deleted.message)
        self.assertFalse(destination.exists())

    def test_glob_and_non_overwriting_copy(self) -> None:
        (self.root / "src").mkdir()
        (self.root / "src" / "one.py").write_text("one\n", encoding="utf-8")
        (self.root / "src" / "two.txt").write_text("two\n", encoding="utf-8")
        matched = self.call("glob_files", pattern="**/*.py")
        self.assertTrue(matched.ok, matched.message)
        self.assertEqual(matched.message, "src/one.py")

        copied = self.call(
            "copy_file", source="src/one.py", destination="backup/one.py"
        )
        self.assertTrue(copied.ok, copied.message)
        self.assertEqual(
            (self.root / "backup" / "one.py").read_text(encoding="utf-8"), "one\n"
        )
        refused = self.call(
            "copy_file", source="src/one.py", destination="backup/one.py"
        )
        self.assertFalse(refused.ok)

    def test_sensitive_internal_paths_are_hidden_and_blocked(self) -> None:
        (self.root / ".env.local").write_text("SECRET=value", encoding="utf-8")
        (self.root / ".env.example").write_text("SECRET=placeholder", encoding="utf-8")
        (self.root / ".forge" / "sessions").mkdir(parents=True)
        (self.root / ".forge" / "sessions" / "private.json").write_text(
            "{}", encoding="utf-8"
        )

        listing = self.call("list_files", path=".")
        self.assertTrue(listing.ok)
        self.assertNotIn(".env.local", listing.message)
        self.assertNotIn(".forge", listing.message)
        self.assertNotIn(".env.example", listing.message)
        for protected in [
            ".env.local",
            ".env.example",
            ".forge/sessions/private.json",
        ]:
            result = self.call("read_file", path=protected)
            self.assertFalse(result.ok)
            self.assertIn("Protected workspace path", result.message)


class CommandPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        test_temp = Path.cwd() / "tmp" / "tests"
        test_temp.mkdir(parents=True, exist_ok=True)
        self.root = test_temp / f"case-{uuid4().hex}"
        self.root.mkdir()
        self.workspace = Workspace(self.root)
        self.policy = CommandPolicy(safe_mode=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_blocks_shell_operators_and_unlisted_programs(self) -> None:
        allowed, reason, _ = self.policy.check("git status && echo bad", self.workspace)
        self.assertFalse(allowed)
        self.assertIn("Shell operators", reason)
        allowed, reason, _ = self.policy.check("powershell Get-ChildItem", self.workspace)
        self.assertFalse(allowed)
        self.assertIn("not allowed", reason)

    def test_blocks_inline_interpreter_code(self) -> None:
        allowed, reason, _ = self.policy.check("python -c pass", self.workspace)
        self.assertFalse(allowed)
        self.assertIn("Inline", reason)

    def test_tool_reports_nonzero_exit_as_failure(self) -> None:
        tool = build_command_tool(self.workspace, self.policy)
        registry = ToolRegistry([tool])
        result = registry.call(
            "run_command", json.dumps({"command": "git show definitely_missing_ref"})
        )
        self.assertFalse(result.ok)
        self.assertNotEqual(result.metadata.get("exit_code"), 0)

    def test_python_command_uses_current_environment(self) -> None:
        tool = build_command_tool(self.workspace, self.policy)
        registry = ToolRegistry([tool])
        result = registry.call("run_command", json.dumps({"command": "python --version"}))
        self.assertTrue(result.ok, result.message)
        expected = f"Python {sys.version_info.major}.{sys.version_info.minor}"
        self.assertIn(expected, result.message)

    def test_blocks_sensitive_relative_path_arguments(self) -> None:
        (self.root / ".env.local").write_text("SECRET=value", encoding="utf-8")
        allowed, reason, _ = self.policy.check("git add .env.local", self.workspace)
        self.assertFalse(allowed)
        self.assertIn("path is blocked", reason)


class ConfirmationTests(unittest.TestCase):
    def test_denied_confirmation_does_not_execute_handler(self) -> None:
        calls: list[dict] = []

        def handler(arguments: dict) -> object:
            calls.append(arguments)
            return ToolResult.success("executed")

        registry = ToolRegistry(
            [
                ToolSpec(
                    "mutate",
                    "mutating test tool",
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {"value": {"type": "string"}},
                        "required": ["value"],
                    },
                    handler,
                    requires_confirmation=True,
                )
            ]
        )
        denied = registry.call(
            "mutate", '{"value":"x"}', confirm=lambda spec, args: False
        )
        self.assertFalse(denied.ok)
        self.assertTrue(denied.metadata["confirmation_required"])
        self.assertTrue(denied.metadata["denied"])
        self.assertEqual(calls, [])

    def test_approved_confirmation_executes_handler(self) -> None:
        registry = ToolRegistry(
            [
                ToolSpec(
                    "mutate",
                    "mutating test tool",
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {"value": {"type": "string"}},
                        "required": ["value"],
                    },
                    lambda arguments: ToolResult.success(arguments["value"]),
                    requires_confirmation=True,
                )
            ]
        )
        approved = registry.call(
            "mutate", '{"value":"x"}', confirm=lambda spec, args: True
        )
        self.assertTrue(approved.ok)
        self.assertEqual(approved.message, "x")


if __name__ == "__main__":
    unittest.main()
