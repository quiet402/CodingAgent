from __future__ import annotations

import unittest

from forge_agent.history import ConversationHistory
from forge_agent.model import OpenAICompatibleClient


class HistoryTests(unittest.TestCase):
    def test_compaction_preserves_assistant_tool_pairs(self) -> None:
        history = ConversationHistory("system", "task", max_chars=8_000)
        for index in range(8):
            history.add(
                {
                    "role": "assistant",
                    "content": "plan " + ("x" * 500),
                    "tool_calls": [
                        {
                            "id": f"call_{index}",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": "{}"},
                        }
                    ],
                }
            )
            history.add(
                {
                    "role": "tool",
                    "tool_call_id": f"call_{index}",
                    "name": "read_file",
                    "content": "observation " + ("y" * 1000),
                }
            )
        messages = history.request_messages()
        self.assertTrue(any("deterministically compacted" in str(m.get("content")) for m in messages))
        assistant_ids = {
            call["id"]
            for message in messages
            for call in message.get("tool_calls", [])
        }
        for message in messages:
            if message.get("role") == "tool":
                self.assertIn(message["tool_call_id"], assistant_ids)


class ModelParsingTests(unittest.TestCase):
    def test_parses_native_tool_call(self) -> None:
        document = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "reasoning_content": "I should inspect the requested file.",
                        "tool_calls": [
                            {
                                "id": "abc",
                                "type": "function",
                                "function": {"name": "read_file", "arguments": '{"path":"x"}'},
                            }
                        ],
                    }
                }
            ]
        }
        turn = OpenAICompatibleClient._parse(document)
        self.assertEqual(turn.tool_calls[0].name, "read_file")
        self.assertEqual(turn.tool_calls[0].id, "abc")
        self.assertEqual(turn.reasoning_content, "I should inspect the requested file.")
        self.assertEqual(
            turn.as_message()["reasoning_content"],
            "I should inspect the requested file.",
        )

    def test_normalizes_preparsed_tool_arguments(self) -> None:
        document = {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "abc",
                                "function": {"name": "read_file", "arguments": {"path": "x"}},
                            }
                        ]
                    }
                }
            ]
        }
        turn = OpenAICompatibleClient._parse(document)
        self.assertEqual(turn.tool_calls[0].arguments, '{"path": "x"}')

    def test_stream_assembles_text_and_fragmented_tool_call(self) -> None:
        lines = [
            b'data: {"choices":[{"delta":{"reasoning_content":"Inspect "}}]}\n',
            b'data: {"choices":[{"delta":{"reasoning_content":"first."}}]}\n',
            b'data: {"choices":[{"delta":{"content":"Plan "}}]}\n',
            b'data: {"choices":[{"delta":{"content":"ready."}}]}\n',
            b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_","function":{"name":"read_","arguments":"{\\"pa"}}]}}]}\n',
            b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"1","function":{"name":"file","arguments":"th\\":\\"x\\"}"}}]}}]}\n',
            b"data: [DONE]\n",
        ]
        fragments: list[str] = []
        turn = OpenAICompatibleClient._parse_stream(lines, fragments.append)
        self.assertEqual("".join(fragments), "Plan ready.")
        self.assertEqual(turn.content, "Plan ready.")
        self.assertEqual(turn.reasoning_content, "Inspect first.")
        self.assertEqual(turn.tool_calls[0].id, "call_1")
        self.assertEqual(turn.tool_calls[0].name, "read_file")
        self.assertEqual(turn.tool_calls[0].arguments, '{"path":"x"}')

    def test_request_includes_provider_reasoning_options(self) -> None:
        client = OpenAICompatibleClient(
            api_key="secret",
            base_url="https://api.deepseek.com",
            model="deepseek-v4-pro",
            thinking="enabled",
            reasoning_effort="high",
        )
        captured: dict = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self):
                return b'{"choices":[{"message":{"content":"ok"}}]}'

        def fake_open(payload):
            captured.update(payload)
            return Response()

        client._open = fake_open  # type: ignore[method-assign]
        turn = client.complete([{"role": "user", "content": "hello"}], [])
        self.assertEqual(turn.content, "ok")
        self.assertEqual(captured["thinking"], {"type": "enabled"})
        self.assertEqual(captured["reasoning_effort"], "high")


if __name__ == "__main__":
    unittest.main()
