"""spawn_subagent: Claude Code-style tool that lets an agent delegate to a registered subagent.

Each spawn is a fresh :class:`~agentdeck.agents.runners.headless.HeadlessRunner` one-shot — no
session, no parent history, the ``task`` text is the subagent's entire context. Depth is capped
via a :class:`~contextvars.ContextVar` so a spawned subagent cannot itself spawn further ones.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING

from agents import function_tool

from agentdeck.agents.registry import AgentRegistry
from agentdeck.errors import NotFoundError

if TYPE_CHECKING:
    from agents.tool import FunctionTool

    from agentdeck.agents.base import BaseAgent

# Matches App._mount_project_dir's alias — kept a literal (not imported from app.py) since a
# registry built lazily at call time is what makes plain ``App()`` usage work: by the time the
# tool actually runs, App.__post_init__ has already mounted the project package under this name.
_PROJECT_ALIAS = "agentdeck_project"

# depth 1: the top-level agent (depth 0) may spawn; a spawned subagent (depth 1) may not spawn
# further ones — no override surface in v1.
_MAX_DEPTH = 1
_depth: ContextVar[int] = ContextVar("agentdeck_subagent_depth", default=0)


def _describe(registry: AgentRegistry, name: str) -> str:
    try:
        return registry.get(name).handoff_description or "(no description)"
    except NotFoundError:
        return "(not currently registered)"


def spawn_subagent_tool(cls: type[BaseAgent], registry: AgentRegistry | None = None) -> FunctionTool:
    """Build the ``spawn_subagent`` tool for ``cls``'s ``subagents`` allowlist.

    ``registry`` defaults to a lazily-scanned :class:`AgentRegistry` over the project package
    mounted by :class:`agentdeck.app.App`; pass one explicitly to test against a fixed roster.
    """
    allowed = tuple(cls.subagents)
    reg = registry if registry is not None else AgentRegistry(_PROJECT_ALIAS)
    roster = "\n".join(f"- {name}: {_describe(reg, name)}" for name in allowed)

    async def spawn_subagent(agent: str, task: str) -> str:
        """Delegate a task to an isolated subagent and return its final output.

        The subagent runs as a one-shot with no session and no shared history with you —
        ``task`` is its *entire* context, so it must include everything the subagent needs.

        Args:
            agent: Name of the agent to spawn; must be one of the allowed agents listed above.
            task: The complete task description the subagent will see.
        """
        if agent not in allowed:
            return f"error: unknown_subagent: {agent!r} is not in the allowed subagents {list(allowed)}"
        if _depth.get() >= _MAX_DEPTH:
            return "error: subagent_depth_exhausted: subagents cannot spawn further subagents"
        try:
            agent_cls = reg.get(agent)
        except NotFoundError as exc:
            return f"error: unknown_subagent: {exc}"

        from agentdeck.agents.runners.headless import HeadlessRunner

        token = _depth.set(_depth.get() + 1)
        try:
            result = await HeadlessRunner.from_agent(agent_cls.build()).run(task)
        finally:
            _depth.reset(token)
        return str(result.final_output)

    description = (
        "Delegate a task to an isolated subagent: it runs one-shot with no shared history — "
        "the task text is its entire context. Returns the subagent's final output as a string.\n"
        f"Allowed agents:\n{roster}"
    )
    return function_tool(spawn_subagent, name_override="spawn_subagent", description_override=description)


__all__ = ["spawn_subagent_tool"]
