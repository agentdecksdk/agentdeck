"""agentdeck — declarative framework over the OpenAI Agents SDK and LangGraph.

Owns *configuration* (settings, capabilities, runner glue, graph compilation,
plug-in discovery) so the underlying engines own *execution*.
:class:`agentdeck.Deck` is the composition root — either constructed directly
from :class:`agentdeck.Agent`/:class:`agentdeck.Workflow` declarations, or
discovered from a project directory (``Deck.from_project()``).
"""

from agentdeck.authoring import Agent, Workflow
from agentdeck.deck import Deck, TurnResult
from agentdeck.errors import AgentdeckError, ConfigError, NotFoundError, SessionBusyError, SkillError, StoreError

__all__ = [
    "Agent",
    "AgentdeckError",
    "ConfigError",
    "Deck",
    "NotFoundError",
    "SessionBusyError",
    "SkillError",
    "StoreError",
    "TurnResult",
    "Workflow",
]
