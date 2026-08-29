from __future__ import annotations

from io import StringIO
from pathlib import Path
import shutil
import unittest
from uuid import uuid4

from forge_agent.agent import AgentRunner
from forge_agent.config import AgentConfig
from forge_agent.model import AssistantTurn, ToolCall
from forge_agent.tools import (
    CommandPolicy,
    ToolRegistry,
    Workspace,
    build_command_tool,
    build_filesystem_tools,
)
from forge_agent.ui import ConsoleUI


class ScriptedClient:
    def __init__(self, turns: list[AssistantTurn]) -> None:
        self.turns = list(turns)
        self.requests: list[list[dict]] = []

    def complete(self, messages, tools, *, on_text=None):
        self.requests.append(messages)
        if not self.turns:
            raise AssertionError("Scripted client ran out of responses")
        turn = self.turns.pop(0)
        if on_text and turn.content:
            midpoint = max(1, len(turn.content) // 2)
            on_text(turn.content[:midpoint])
            on_text(turn.content[midpoint:])
        return turn


class AgentLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        test_temp = Path.cwd() / "tmp" / "tests"
        test_temp.mkdir(parents=True, exist_ok=True)
        self.root = test_temp / f"case-{uuid4().hex}"
        self.root.mkdir()
        self.config = AgentConfig(self.root, "test-key", max_steps=8)
        workspace = Workspace(self.root)
        specs = build_filesystem_tools(workspace)
        specs.append(build_command_tool(workspace, CommandPolicy()))
        self.registry = ToolRegistry(specs)
        self.output = StringIO()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def runner(self, client: ScriptedClient) -> AgentRunner:
        return AgentRunner(client, self.registry, self.config, ui=ConsoleUI(self.output))

    def test_model_tool_observation_model_cycle(self) -> None:
        client = ScriptedClient(
            [
                AssistantTurn(
                    "I will create a focused solution.",
                    [ToolCall("call_1", "write_file", '{"path":"answer.py","content":"print(42)\\n"}')],
                    "I should use the workspace-scoped write tool.",
                ),
                AssistantTurn("Created answer.py and verified the requested implementation."),
            ]
        )
        result = self.runner(client).run("Create answer.py")

        self.assertEqual(result.stop_reason, "completed")
        self.assertEqual(result.tool_calls, 1)
        self.assertEqual(result.changed_files, ["answer.py"])
        self.assertEqual((self.root / "answer.py").read_text(), "print(42)\n")
        tool_message = client.requests[1][-1]
        assistant_message = client.requests[1][-2]
        self.assertEqual(
            assistant_message["reasoning_content"],
            "I should use the workspace-scoped write tool.",
        )
        self.assertEqual(tool_message["role"], "tool")
        self.assertEqual(tool_message["tool_call_id"], "call_1")
        self.assertTrue(Path(result.audit_path).is_file())

    def test_malformed_tool_arguments_return_to_model(self) -> None:
        client = ScriptedClient(
            [
                AssistantTurn("", [ToolCall("bad", "read_file", "not-json")]),
                AssistantTurn("Recovered after observing the validation error."),
            ]
        )
        result = self.runner(client).run("Inspect a file")
        self.assertEqual(result.stop_reason, "completed")
        self.assertEqual(result.tool_errors, 1)
        self.assertIn("not valid JSON", client.requests[1][-1]["content"])

    def test_third_identical_call_is_blocked_by_loop_guard(self) -> None:
        (self.root / "a.txt").write_text("a", encoding="utf-8")
        call = lambda number: AssistantTurn(
            "", [ToolCall(f"call_{number}", "read_file", '{"path":"a.txt"}')]
        )
        client = ScriptedClient([call(1), call(2), call(3), AssistantTurn("Stopped repeating.")])
        result = self.runner(client).run("Read once")
        self.assertEqual(result.stop_reason, "completed")
        self.assertEqual(result.tool_errors, 1)
        self.assertIn("Loop guard", client.requests[3][-1]["content"])

    def test_acceptance_flow_reads_edits_and_runs_tests(self) -> None:
        (self.root / "inventory.py").write_text(
            "def discounted_price(price, discount_percent):\n"
            "    return price - discount_percent\n",
            encoding="utf-8",
        )
        (self.root / "demo_tests.py").write_text(
            "import unittest\n"
            "from inventory import discounted_price\n"
            "class PriceTests(unittest.TestCase):\n"
            "    def test_price(self):\n"
            "        self.assertEqual(discounted_price(200, 15), 170.0)\n"
            "    def test_invalid(self):\n"
            "        with self.assertRaises(ValueError):\n"
            "            discounted_price(-1, 10)\n",
            encoding="utf-8",
        )
        client = ScriptedClient(
            [
                AssistantTurn("Inspecting files.", [ToolCall("ls", "list_files", "{}")] ),
                AssistantTurn("Reading implementation.", [ToolCall("read", "read_file", '{"path":"inventory.py"}')]),
                AssistantTurn(
                    "Applying a focused fix.",
                    [
                        ToolCall(
                            "edit",
                            "replace_in_file",
                            '{"path":"inventory.py","old_text":"    return price - discount_percent","new_text":"    if price < 0 or not 0 <= discount_percent <= 100:\\n        raise ValueError(\\"invalid price or discount\\")\\n    return round(price * (1 - discount_percent / 100), 2)"}',
                        )
                    ],
                ),
                AssistantTurn(
                    "Running the complete test file.",
                    [ToolCall("test", "run_command", '{"command":"python -m unittest demo_tests.py -v"}')],
                ),
                AssistantTurn("Fixed percentage calculation and validation; all tests pass."),
            ]
        )
        result = self.runner(client).run("Fix the pricing bug and run tests")
        self.assertEqual(result.stop_reason, "completed")
        self.assertEqual(result.tool_calls, 4)
        self.assertEqual(result.tool_errors, 0)
        self.assertIn("exit_code", client.requests[4][-1]["content"])
        self.assertIn("discount_percent / 100", (self.root / "inventory.py").read_text())

    def test_continuous_session_retains_previous_turn(self) -> None:
        client = ScriptedClient(
            [
                AssistantTurn("First task completed."),
                AssistantTurn("Second task used the earlier context."),
            ]
        )
        runner = self.runner(client)
        first = runner.run("Inspect the repository")
        second = runner.run("Now explain the change", continue_session=True)
        self.assertEqual(first.stop_reason, "completed")
        self.assertEqual(second.stop_reason, "completed")
        second_request = client.requests[1]
        self.assertTrue(
            any(message.get("content") == "First task completed." for message in second_request)
        )
        self.assertTrue(
            any(message.get("content") == "Now explain the change" for message in second_request)
        )

    def test_reset_session_discards_previous_history(self) -> None:
        client = ScriptedClient([AssistantTurn("one"), AssistantTurn("two")])
        runner = self.runner(client)
        runner.run("first")
        first_audit = runner.audit.path
        runner.reset_session()
        runner.run("second", continue_session=True)
        self.assertNotEqual(first_audit, runner.audit.path)
        self.assertFalse(
            any(message.get("content") == "one" for message in client.requests[1])
        )

    def test_session_survives_runner_restart_without_persisting_api_key(self) -> None:
        first_client = ScriptedClient(
            [AssistantTurn("First persisted answer.", reasoning_content="private context")]
        )
        first_runner = self.runner(first_client)
        first_runner.run("Remember this task")
        session_id = first_runner.audit.session_id
        session_path = Path(first_runner.session_path)
        self.assertTrue(session_path.is_file())
        self.assertNotIn("test-key", session_path.read_text(encoding="utf-8"))

        second_client = ScriptedClient([AssistantTurn("Resumed successfully.")])
        second_runner = self.runner(second_client)
        loaded = second_runner.resume_session(session_id)
        second_runner.run("Continue after restart", continue_session=True)

        self.assertEqual(loaded.session_id, session_id)
        request = second_client.requests[0]
        self.assertTrue(
            any(message.get("content") == "First persisted answer." for message in request)
        )
        self.assertTrue(
            any(message.get("reasoning_content") == "private context" for message in request)
        )
        self.assertTrue(
            any(message.get("content") == "Continue after restart" for message in request)
        )


if __name__ == "__main__":
    unittest.main()
