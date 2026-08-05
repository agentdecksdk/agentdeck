"""Legacy path, kept working: the real implementation relocated to
``agentdeck.adapters.engines.langgraph.checkpointer`` (ADR-D5: a checkpointer is an
engine's own working memory, so it belongs in the engine's adapter directory, not in
``runtime``) — the same move the openai-agents adapter made for ``SessionFactory``.
``agentdeck.workflows.base`` (v1, frozen behavior) still imports this module and this
name — re-exported here, translating v1's ``CheckpointSettings`` into the adapter's plain
``(backend, url)`` signature, so v1 stays byte-for-byte unchanged rather than moving.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentdeck.adapters.engines.langgraph.checkpointer import _memory_saver, _postgres_saver, _run_sync, _sqlite_saver
from agentdeck.adapters.engines.langgraph.checkpointer import resolve_checkpointer as _resolve_checkpointer

if TYPE_CHECKING:
    from langgraph.checkpoint.base import BaseCheckpointSaver

    from agentdeck.runtime.settings import CheckpointSettings


def resolve_checkpointer(settings: CheckpointSettings) -> BaseCheckpointSaver:
    """Build the checkpointer named by ``settings.backend``: ``ValueError`` for an unknown
    one, ``ImportError`` (with an install hint) for sqlite/postgres without the
    ``[durability]`` extra."""
    return _resolve_checkpointer(settings.backend, settings.url)


__all__ = ["_memory_saver", "_postgres_saver", "_run_sync", "_sqlite_saver", "resolve_checkpointer"]
