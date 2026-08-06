"""agentdeck — declarative framework over the OpenAI Agents SDK and LangGraph.

Owns *configuration* (settings, capabilities, sandbox manifest, runner glue,
graph compilation, plug-in discovery) so the underlying engines own *execution*.
Application code typically imports from :mod:`agentdeck.agents` and
:mod:`agentdeck.workflows`; :mod:`agentdeck.runtime` and :mod:`agentdeck.skills`
are consumed indirectly through those facades. :class:`agentdeck.App` is the
single entry point that discovers and instantiates a project's agents,
workflows, and skills.
"""

from agentdeck.app import App, TurnResult
from agentdeck.errors import AgentdeckError, ConfigError, NotFoundError, SessionBusyError, SkillError, StoreError

__all__ = [
    "AgentdeckError",
    "App",
    "ConfigError",
    "NotFoundError",
    "SessionBusyError",
    "SkillError",
    "StoreError",
    "TurnResult",
]
