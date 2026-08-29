from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import unittest
from uuid import uuid4

from forge_agent.tools import ToolRegistry, Workspace, build_git_tools


class GitToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / "tmp" / "tests" / f"git-{uuid4().hex}"
        self.root.mkdir(parents=True)
        subprocess.run(
            ["git", "init", "-q", "--initial-branch=main"],
            cwd=self.root,
            check=True,
        )
        (self.root / "sample.txt").write_text("one\n", encoding="utf-8")
        subprocess.run(["git", "add", "sample.txt"], cwd=self.root, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Forge Test",
                "-c",
                "user.email=forge@example.invalid",
                "commit",
                "-q",
                "-m",
                "initial",
            ],
            cwd=self.root,
            check=True,
        )
        self.registry = ToolRegistry(build_git_tools(Workspace(self.root)))

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def call(self, name: str, **arguments: object):
        return self.registry.call(name, json.dumps(arguments))

    def test_status_diff_and_log_are_read_only(self) -> None:
        (self.root / "sample.txt").write_text("one\ntwo\n", encoding="utf-8")
        status = self.call("git_status")
        self.assertTrue(status.ok, status.message)
        self.assertIn("main", status.message)
        self.assertIn("sample.txt", status.message)

        diff = self.call("git_diff", path="sample.txt", context_lines=1)
        self.assertTrue(diff.ok, diff.message)
        self.assertIn("+two", diff.message)

        log = self.call("git_log", limit=1)
        self.assertTrue(log.ok, log.message)
        self.assertIn("initial", log.message)

    def test_staged_diff_and_sensitive_path_guard(self) -> None:
        (self.root / "sample.txt").write_text("changed\n", encoding="utf-8")
        subprocess.run(["git", "add", "sample.txt"], cwd=self.root, check=True)
        staged = self.call("git_diff", staged=True)
        self.assertTrue(staged.ok, staged.message)
        self.assertIn("+changed", staged.message)

        (self.root / ".env.local").write_text("SECRET=value", encoding="utf-8")
        protected = self.call("git_diff", path=".env.local")
        self.assertFalse(protected.ok)
        self.assertIn("Protected workspace path", protected.message)


if __name__ == "__main__":
    unittest.main()
