"""Wires a :class:`~agentdeck.skills.Skills` registry into ``compile_agent``'s
``resolve_skills`` hook: the disclosure text an agent's instructions gain, and the one
``load_skill`` tool that lets the model read a skill's full body once it decides to use it.

Kept in ``authoring/`` rather than ``agentdeck.skills``: building a ``FunctionTool`` needs the
Agents SDK directly, which ``agentdeck.skills`` stays free of.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agents import function_tool

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from agents.tool import FunctionTool

    from agentdeck.skills import Skills

_LOAD_SKILL_DESCRIPTION = (
    "Read the full SKILL.md instructions for one of this agent's available skills, named in "
    "the skills section of your instructions. Call this before following a skill you have "
    "decided to use."
)


def skills_resolver(skills: Skills) -> Callable[[Sequence[str]], tuple[str, list[FunctionTool]]]:
    """Build the ``resolve_skills`` callable ``compile_agent`` expects, scoped to one
    :class:`Skills` registry. ``compile_agent`` calls it once per agent with that agent's own
    ``skills=[...]``, so the ``load_skill`` tool it returns is scoped to that agent's names  -
    a skill outside its own allow-list stays unreachable even though the registry knows it.
    """

    def _resolve(names: Sequence[str]) -> tuple[str, list[FunctionTool]]:
        return skills.disclosure_text(names), [_load_skill_tool(skills, frozenset(names))]

    return _resolve


def _load_skill(skills: Skills, allowed: frozenset[str], name: str) -> str:
    if name not in allowed:
        return f"error: skill_not_available: {name!r} is not one of this agent's skills: {sorted(allowed)}."
    return skills.get(name).body


def _load_skill_tool(skills: Skills, allowed: frozenset[str]) -> FunctionTool:
    @function_tool(name_override="load_skill", description_override=_LOAD_SKILL_DESCRIPTION)
    async def load_skill(name: str) -> str:
        return _load_skill(skills, allowed, name)

    return load_skill


__all__ = ["skills_resolver"]
