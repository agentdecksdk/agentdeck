"""The openai-agents engine adapter: ``EnginePort`` over ``agents.Runner``."""

from __future__ import annotations

from agentdeck.adapters.engines.openai_agents.engine import OpenAIAgentsEngine
from agentdeck.adapters.engines.openai_agents.sessions import ExecutionStore, SessionFactory

# compat.V1CompatEngine is deliberately not re-exported: it imports v1's runner glue, and
# a v2 caller importing this package should not pull v1 in behind its back.
__all__ = ["ExecutionStore", "OpenAIAgentsEngine", "SessionFactory"]
