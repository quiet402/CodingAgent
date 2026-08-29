"""Configuration with CLI > environment > default precedence."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(slots=True)
class AgentConfig:
    workspace: Path
    api_key: str
    provider: str = "openai"
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-5.6"
    thinking: str | None = None
    reasoning_effort: str | None = None
    max_steps: int = 24
    context_chars: int = 90_000
    tool_output_chars: int = 30_000
    command_timeout: int = 30
    safe_mode: bool = True
    stream: bool = True
    api_retries: int = 3

    @classmethod
    def from_env(cls, workspace: str | Path = ".", **overrides: object) -> "AgentConfig":
        """Build configuration while keeping secrets out of files and arguments."""
        provider = str(overrides.get("provider") or os.getenv("FORGE_PROVIDER", "openai")).casefold()
        profiles = {
            "openai": ("https://api.openai.com/v1", "gpt-5.6", None, None),
            "deepseek": ("https://api.deepseek.com", "deepseek-v4-pro", "enabled", "high"),
            "custom": ("https://api.openai.com/v1", "gpt-5.6", None, None),
        }
        if provider not in profiles:
            choices = ", ".join(profiles)
            raise ValueError(f"Unknown provider {provider!r}; choose one of: {choices}")
        default_base_url, default_model, default_thinking, default_effort = profiles[provider]

        if provider == "deepseek":
            provider_key = os.getenv("DEEPSEEK_API_KEY", "")
            provider_base_url = os.getenv("DEEPSEEK_BASE_URL")
            provider_model = os.getenv("DEEPSEEK_MODEL")
        else:
            provider_key = os.getenv("OPENAI_API_KEY", "")
            provider_base_url = os.getenv("OPENAI_BASE_URL")
            provider_model = os.getenv("OPENAI_MODEL")

        values: dict[str, object] = {
            "workspace": Path(workspace).resolve(),
            "provider": provider,
            "api_key": os.getenv("FORGE_API_KEY") or provider_key,
            "base_url": os.getenv("FORGE_BASE_URL") or provider_base_url or default_base_url,
            "model": os.getenv("FORGE_MODEL") or provider_model or default_model,
            "thinking": os.getenv("FORGE_THINKING") or default_thinking,
            "reasoning_effort": os.getenv("FORGE_REASONING_EFFORT") or default_effort,
            "max_steps": int(os.getenv("FORGE_MAX_STEPS", "24")),
        }
        values.update(
            {
                key: value
                for key, value in overrides.items()
                if value is not None and key != "provider"
            }
        )
        config = cls(**values)  # type: ignore[arg-type]
        config.validate()
        return config

    def validate(self) -> None:
        if self.provider not in {"openai", "deepseek", "custom"}:
            raise ValueError("provider must be openai, deepseek, or custom")
        if self.provider == "deepseek" and not self.api_key:
            raise ValueError(
                "DeepSeek API key is missing; set DEEPSEEK_API_KEY or FORGE_API_KEY"
            )
        if not self.workspace.is_dir():
            raise ValueError(f"Workspace does not exist: {self.workspace}")
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("Base URL must start with http:// or https://")
        if self.thinking not in {None, "enabled", "disabled"}:
            raise ValueError("thinking must be enabled or disabled")
        if self.reasoning_effort not in {None, "low", "high", "max"}:
            raise ValueError("reasoning_effort must be low, high, or max")
        if not 1 <= self.max_steps <= 200:
            raise ValueError("max_steps must be between 1 and 200")
        if self.context_chars < 8_000:
            raise ValueError("context_chars must be at least 8000")
