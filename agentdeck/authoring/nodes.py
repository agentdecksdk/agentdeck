"""Workflow node callables that bridge into agent execution or the host filesystem.

Moved here from v1's ``workflows/nodes.py`` (plan-phase4-deck.md, 4a): they compile to
whatever a graph node needs, the same as ``Agent``/``Workflow`` compile to an
``InvocableSpec``. ``SkillNode`` is not among them — deleted per ``plan-skills.md``: a
workflow that needs a skill uses an agent node whose agent declares ``skills=[...]``,
one meaning for "skill" everywhere rather than a second, workflow-only executable one.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from langgraph.config import get_config, get_stream_writer

from agentdeck.adapters.engines.langgraph.engine import STREAM_CONFIGURABLE_KEY
from agentdeck.authoring.agent import Agent
from agentdeck.authoring.compile import compile_agent
from agentdeck.authoring.runners.agent import HeadlessRunner, StreamDone

logger = logging.getLogger(__name__)

PathBuilder = Callable[[Any], "str | Path | None"]
TextParser = Callable[[str], Any]


class LoadFileNode:
    """Read a file emitted by an upstream node into ``into``.

    ``path(state)`` returns the file path or ``None`` (no-op so the node can
    sit downstream of optional stages). The path must be absolute: a relative
    one used to resolve inside the run's sandbox, and sandboxing is not part of
    v3.
    """

    __slots__ = ("path", "into", "parse")

    def __init__(
        self,
        *,
        path: PathBuilder,
        into: str,
        parse: TextParser | None = None,
    ) -> None:
        # NOTE: ``path`` must read from state at call time; static strings here
        # are almost always a mistake (a missing ``lambda s: s.field``).
        if not callable(path):
            raise TypeError(
                f"LoadFileNode(path=...) expects a callable (state -> path); got {path!r}.",
            )
        self.path = path
        self.into = into
        self.parse = parse

    async def __call__(self, state: Any) -> dict[str, Any]:
        target = self.path(state)
        if not target:
            return {}
        target_path = Path(str(target))
        # Refusing rather than resolving against the cwd is deliberate: a relative path here
        # was always read inside the sandbox, and quietly reading the host fs instead would
        # widen exactly what the sandbox was there to narrow.
        if not target_path.is_absolute():
            raise RuntimeError(
                f"LoadFileNode(path=...) needs an absolute path; got {str(target)!r}. "
                "Relative paths resolved inside the sandbox, which v3 does not ship.",
            )
        text = target_path.read_text(encoding="utf-8")
        return {self.into: self.parse(text) if self.parse else text}


class AgentNode:
    """Run an :class:`~agentdeck.authoring.agent.Agent` as a graph node.

    Reads the prompt from ``state[input_key]`` and writes the agent's ``final_output`` to
    ``state[output_key]``. Forwards the nested agent's text deltas into the graph's custom
    stream (``get_stream_writer()``) so ``run_workflow_stream`` surfaces them too.

    Compiles ``agent`` standalone on first call — MCP servers resolve the same way a
    root agent's do, but ``handoffs=``/``skills=`` that need a ``Deck``'s catalog do not:
    an agent used inside a workflow node is compiled with no catalog in view.
    """

    __slots__ = ("agent", "input_key", "output_key", "_built")

    def __init__(
        self,
        agent: Agent,
        *,
        input_key: str = "input",
        output_key: str = "output",
    ) -> None:
        if not isinstance(agent, Agent):
            raise TypeError(f"AgentNode(agent=...) expects an Agent; got {agent!r}.")
        self.agent = agent
        self.input_key = input_key
        self.output_key = output_key
        # Reused across every run, not recompiled: an agent that names no model gets
        # `OPENAI_MODEL` baked onto it at this first compile (`authoring.compile.compile_agent`),
        # so a settings change afterwards — even paired with `reset_settings_cache()` — has
        # nothing to reach. A test compiling a node has to account for that staleness rather
        # than assume each run re-resolves it.
        self._built: Any | None = None

    async def __call__(self, state: Any) -> dict[str, Any]:
        if self._built is None:
            self._built = compile_agent(self.agent)
        logger.debug("agent node %s: start", self.agent.name)
        runner = HeadlessRunner.from_agent(self._built)
        prompt = _read(state, self.input_key)
        if not _is_streaming():
            result = await runner.run(prompt)
            return {self.output_key: result.final_output}
        writer = get_stream_writer()
        done: Any = None
        async for chunk in runner.run_streamed(prompt):
            if isinstance(chunk, StreamDone):
                done = chunk
            else:
                writer(chunk)
        return {self.output_key: done.final_output}


def _is_streaming() -> bool:
    """``True`` only inside ``DevWorkflowRunner.run_stream``'s ``astream`` call."""
    return bool(get_config().get("configurable", {}).get(STREAM_CONFIGURABLE_KEY))


def _read(state: Any, key: str | None) -> Any:
    if key is None:
        return None
    if isinstance(state, Mapping):
        return state.get(key)
    return getattr(state, key, None)


__all__ = ["AgentNode", "LoadFileNode"]
