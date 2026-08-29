from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch
import unittest

from forge_agent.config import AgentConfig


class ConfigTests(unittest.TestCase):
    def test_deepseek_profile_uses_current_defaults_and_dedicated_key(self) -> None:
        with patch.dict(
            os.environ,
            {"FORGE_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": "deepseek-secret"},
            clear=True,
        ):
            config = AgentConfig.from_env(Path.cwd())
        self.assertEqual(config.provider, "deepseek")
        self.assertEqual(config.api_key, "deepseek-secret")
        self.assertEqual(config.base_url, "https://api.deepseek.com")
        self.assertEqual(config.model, "deepseek-v4-pro")
        self.assertEqual(config.thinking, "enabled")
        self.assertEqual(config.reasoning_effort, "high")

    def test_cli_style_overrides_win_over_provider_defaults(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = AgentConfig.from_env(
                Path.cwd(),
                provider="deepseek",
                api_key="deepseek-secret",
                model="deepseek-v4-flash",
                thinking="disabled",
            )
        self.assertEqual(config.model, "deepseek-v4-flash")
        self.assertEqual(config.thinking, "disabled")

    def test_rejects_unknown_provider(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "Unknown provider"):
                AgentConfig.from_env(Path.cwd(), provider="unknown")

    def test_deepseek_fails_early_without_key(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "DeepSeek API key is missing"):
                AgentConfig.from_env(Path.cwd(), provider="deepseek")


if __name__ == "__main__":
    unittest.main()
