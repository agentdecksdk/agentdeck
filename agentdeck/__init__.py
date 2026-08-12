"""agentdeck — declarative framework over the OpenAI Agents SDK and LangGraph.

Owns *configuration* (settings, capabilities, runner glue, graph compilation,
plug-in discovery) so the underlying engines own *execution*.
:class:`agentdeck.Deck` is the composition root — either constructed directly
from :class:`agentdeck.Agent`/:class:`agentdeck.Workflow` declarations, or
discovered from a project directory (``Deck.from_project()``).
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _version

from agentdeck.authoring import Agent, Workflow
from agentdeck.core.context import Context
from agentdeck.deck import Deck, TurnResult
from agentdeck.errors import (
    AgentdeckError,
    ConfigError,
    ContextTypeError,
    NotFoundError,
    SessionBusyError,
    SkillError,
    StoreError,
)

try:
    # The *distribution* is `agentdeck-sdk`; the import package is `agentdeck`. They differ
    # because PyPI refuses `agentdeck` as too similar to the squatted `agent-deck` placeholder.
    # Passing the import name here returns nothing and falls through to "0+unknown" — a silently
    # wrong version, which is the exact failure #176 added `__version__` to prevent.
    __version__ = _version("agentdeck-sdk")
except PackageNotFoundError:
    # Running from a source checkout with no installed distribution (e.g. no `pip install -e .`
    # yet) — a version string is still expected of every attribute lookup, not a raise.
    __version__ = "0+unknown"

__all__ = [
    "Agent",
    "AgentdeckError",
    "ConfigError",
    "Context",
    "ContextTypeError",
    "Deck",
    "NotFoundError",
    "SessionBusyError",
    "SkillError",
    "StoreError",
    "TurnResult",
    "Workflow",
    "__version__",
]
