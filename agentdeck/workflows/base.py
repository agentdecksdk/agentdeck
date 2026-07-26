"""Sandbox-aware ``BaseWorkflow`` over LangGraph state graphs."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, ClassVar

from langgraph.graph import END, START
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel

from agentdeck.workflows.state import dump_state

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from agents.tool import FunctionTool
    from langgraph.graph import StateGraph
    from langgraph.graph.state import CompiledStateGraph

    from agentdeck.workflows.runners.dev import DevWorkflowRunner

_CAMEL_RE = re.compile(r"(?<!^)(?=[A-Z])")


class BaseWorkflow:
    """Declarative LangGraph workflow with a shared sandbox.

    Override :attr:`state` (a Pydantic model) and :meth:`build_graph`
    (returns an unbuilt ``StateGraph``). :meth:`run` opens one
    :class:`Workspace` for the whole graph; all nodes — and any agents
    they invoke — share that session via ``Workspace.require()``.
    """

    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    state: ClassVar[type]

    # ``cls.__dict__.get`` (vs. ``getattr``) prevents subclasses from
    # inheriting the parent's compiled graph.
    _compiled: ClassVar[CompiledStateGraph[Any] | None] = None

    @classmethod
    def build_graph(cls) -> StateGraph[Any]:
        raise NotImplementedError(
            f"{cls.__name__}.build_graph() must return a langgraph StateGraph.",
        )

    @classmethod
    def build(cls) -> CompiledStateGraph[Any]:
        """Return the compiled graph, building (and caching) on first call."""
        if cls.__dict__.get("_compiled") is None:
            cls._compiled = cls.build_graph().compile()
        compiled: CompiledStateGraph[Any, None, Any, Any] | None = cls._compiled
        assert compiled is not None  # set above; narrows ClassVar Optional
        return compiled

    @classmethod
    def runner(cls, **runner_options: Any) -> DevWorkflowRunner:
        from agentdeck.workflows.runners.dev import DevWorkflowRunner

        return DevWorkflowRunner.from_workflow(cls, **runner_options)

    @classmethod
    async def run(cls, state: Any = None, **runner_options: Any) -> Any:
        return await cls.runner(**runner_options).run(state)

    @classmethod
    def as_tool(
        cls,
        *,
        name: str | None = None,
        description: str | None = None,
        output_keys: Sequence[str] | None = None,
        defaults: dict[str, Any] | None = None,
        strict_json_schema: bool = False,
    ) -> FunctionTool:
        """Expose this workflow as a :class:`agents.tool.FunctionTool`.

        ``output_keys`` filters the final state to a subset of channels.
        ``defaults`` pins specific workflow-state fields to fixed values
        regardless of the LLM's argument payload — useful for binding
        knobs the agent should not toggle (e.g. ``interactive=True``).
        Pinned fields are stripped from the JSON schema the LLM sees so
        the model does not even try to set them.

        Strict JSON schema is off by default — small chat-completions
        models stall on multi-required-field schemas.
        """
        from agents.tool import FunctionTool

        if not issubclass(cls.state, BaseModel):
            raise TypeError(
                f"{cls.__name__}.state must be a Pydantic model to be exposed as a tool; got {cls.state!r}.",
            )
        keys = tuple(output_keys) if output_keys is not None else None
        pinned = dict(defaults) if defaults else {}
        cls.build()  # surface graph build errors at tool-construction time

        async def on_invoke(_ctx: Any, raw_args: str) -> str:
            args = json.loads(raw_args) if raw_args else {}
            if pinned:
                args = {**args, **pinned}  # pinned values always win
            # Through cls.run so the workflow inherits an active Workspace
            # from the calling agent.
            result = await cls.run(args)
            if keys is not None:
                result: dict[str, Any] = {k: result.get(k) for k in keys}
            return dump_state(result)

        schema = cls.state.model_json_schema()
        if pinned:
            schema = _strip_schema_fields(schema, pinned.keys())

        return FunctionTool(
            name=name or _CAMEL_RE.sub("_", cls.__name__).lower(),
            description=description or cls._tool_description(),
            params_json_schema=schema,
            on_invoke_tool=on_invoke,
            strict_json_schema=strict_json_schema,
        )

    @classmethod
    def node_names(cls) -> list[str]:
        """Node names on the graph excluding START/END.

        Reads the unbuilt :class:`StateGraph` so info-style introspection
        works even if compilation would fail.
        """
        return [n for n in cls.build_graph().nodes if n not in {START, END}]

    @classmethod
    def _tool_description(cls) -> str:
        if cls.description:
            return cls.description.strip()
        if cls.__doc__:
            return cls.__doc__.strip().splitlines()[0]
        return f"Run the {cls.__name__} workflow."


def _strip_schema_fields(schema: dict[str, Any], fields: Iterable[str]) -> dict[str, Any]:
    """Return ``schema`` with ``fields`` removed from ``properties`` and ``required``."""
    drop = set(fields)
    out = dict(schema)
    if isinstance(props := out.get("properties"), dict):
        out["properties"] = {k: v for k, v in props.items() if k not in drop}
    if isinstance(required := out.get("required"), list):
        out["required"] = [r for r in required if r not in drop]
    return out


__all__ = ["BaseWorkflow"]
