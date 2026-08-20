"""The openai-agents engine adapter: ``Executor`` over ``agents.Runner``."""

from __future__ import annotations

from agentdeck.adapters.executors.openai_agents.executor import OpenAIAgentsExecutor
from agentdeck.adapters.executors.openai_agents.runconfig import RunSettings
from agentdeck.adapters.executors.openai_agents.sessions import ExecutionStore, SessionFactory

__all__ = ["ExecutionStore", "OpenAIAgentsExecutor", "RunSettings", "SessionFactory"]
