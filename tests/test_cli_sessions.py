from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import shutil
from unittest.mock import patch
import unittest
from uuid import uuid4

from forge_agent.agent import AgentRunner
from forge_agent.cli import build_parser, run_repl
from forge_agent.config import AgentConfig
from forge_agent.model import AssistantTurn
from forge_agent.tools import ToolRegistry
from forge_agent.ui import ConsoleUI


class OneTurnClient:
    def __init__(self) -> None:
        self.used = False

    def complete(self, messages, tools, *, on_text=None):
        if self.used:
            raise AssertionError("Unexpected model request")
        self.used = True
        return AssistantTurn("saved answer")


class SessionCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / "tmp" / "tests" / f"cli-{uuid4().hex}"
        self.root.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_lists_resumes_and_reports_current_session(self) -> None:
        output = StringIO()
        runner = AgentRunner(
            OneTurnClient(),
            ToolRegistry([]),
            AgentConfig(self.root, "test-key", stream=False),
            ui=ConsoleUI(output),
        )
        runner.run("persist me")
        session_id = runner.audit.session_id

        commands = ["/sessions", "/new", "/resume 1", "/history", "/quit"]
        with patch("builtins.input", side_effect=commands), redirect_stdout(output):
            exit_code = run_repl(runner)

        text = output.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("Saved sessions", text)
        self.assertIn("[1]", text)
        self.assertIn(f"Resumed {session_id}", text)
        self.assertIn(f"session={session_id}", text)
        self.assertIn("history=", text)

    def test_auto_approve_flag_is_available(self) -> None:
        args = build_parser().parse_args(["--yes"])
        self.assertTrue(args.auto_approve)


if __name__ == "__main__":
    unittest.main()
