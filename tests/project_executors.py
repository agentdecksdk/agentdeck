"""The executor set a discovered project runs on, assembled the way the composition root does.

A test that wants a Runtime over a ``.agentdeck/`` project needs every executor wired with the
same resolved settings the deck wires them with; spelling that out per test file is how the two
quietly stop being the same thing. ``test_composition.py`` pins this against what ``Deck``
actually built, so the copy cannot drift without failing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentdeck.adapters.executors.native import NativeExecutor
from agentdeck.adapters.executors.openai_agents import OpenAIAgentsExecutor
from agentdeck.composition import resolve_run_settings

if TYPE_CHECKING:
    from agentdeck.core.ports import Executor


def project_executors() -> tuple[Executor, ...]:
    return (
        OpenAIAgentsExecutor(settings=resolve_run_settings()),
        NativeExecutor(),
    )


__all__ = ["project_executors"]
