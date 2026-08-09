"""agentdeck — declarative framework over the OpenAI Agents SDK and LangGraph.

Owns *configuration* (settings, capabilities, runner glue, graph compilation,
plug-in discovery) so the underlying engines own *execution*. Application code
typically imports from :mod:`agentdeck.authoring` (``Agent``, ``Workflow``);
:mod:`agentdeck.runtime` and :mod:`agentdeck.skills` are consumed indirectly.
:class:`agentdeck.Deck` is the composition root — either constructed directly
(``Deck(agents=..., ...)``) or discovered from a project directory
(``Deck.from_project()``).
"""

from agentdeck.deck import Deck, TurnResult
from agentdeck.errors import AgentdeckError, ConfigError, NotFoundError, SessionBusyError, SkillError, StoreError

__all__ = [
    "AgentdeckError",
    "ConfigError",
    "Deck",
    "NotFoundError",
    "SessionBusyError",
    "SkillError",
    "StoreError",
    "TurnResult",
]
