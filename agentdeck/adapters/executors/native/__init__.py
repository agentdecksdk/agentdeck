"""AgentDeck's own executor: a ``@tool``/``@workflow`` body, run as the coroutine it is."""

from agentdeck.adapters.executors.native.executor import NativeExecutor

__all__ = ["NativeExecutor"]
