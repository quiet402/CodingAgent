"""CodingAgent: a small, auditable coding agent built without agent frameworks."""

from .agent import AgentResult, AgentRunner
from .config import AgentConfig

__all__ = ["AgentConfig", "AgentResult", "AgentRunner"]
__version__ = "0.8.2"
