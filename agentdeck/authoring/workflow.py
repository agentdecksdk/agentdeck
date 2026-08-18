"""``Workflow``: the construction API over a LangGraph state graph, plus the declaration it can
start from  -  mirrors :mod:`agentdeck.authoring.agent`'s ``Agent``/``AgentDeclaration`` pair.

A graph is imperative (nodes, edges) rather than a handful of values, so unlike ``Agent`` there
is no keyword-argument replacement for it: ``WorkflowDeclaration.build_graph()`` stays an
overridable classmethod, and ``Workflow(...)`` wraps one (``base=``) or a bare graph factory
(``graph=``) into the one instance a ``Deck`` can hold as a root  -  the same override-on-
construction shape ``Agent(base=...)`` has, so the two constructors read as one pattern.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel

from agentdeck.errors import DOCS_URL, ConfigError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterable, Sequence

    from agents.tool import FunctionTool
    from langgraph.graph import StateGraph

    from agentdeck.authoring.interrupts import InterruptResult

_UNSET: Any = object()
_CAMEL_RE = re.compile(r"(?<!^)(?=[A-Z])")
_WORKFLOWS_DOCS = f"{DOCS_URL}/build-your-deck/workflows"


class WorkflowDeclaration:
    """Reusable defaults for :class:`Workflow`. Override :attr:`state` and :meth:`build_graph`.

    Renamed from v1's ``BaseWorkflow`` (ruling 10, plan-phase4-deck.md): a declarative input to
    ``Workflow(...)``, never invoked directly.
    """

    name: ClassVar[str | None] = None
    description: ClassVar[str] = ""
    state: ClassVar[type]
    durable: ClassVar[bool] = False

    @classmethod
    def build_graph(cls) -> StateGraph[Any]:
        raise NotImplementedError(f"{cls.__name__}.build_graph() must return a langgraph StateGraph.")


class Workflow:
    """A declarative workflow root: a name, its state model, whether it durably checkpoints,
    and where its graph comes from  -  either ``base=`` (a :class:`WorkflowDeclaration` subclass)
    or ``graph=`` (a bare ``() -> StateGraph`` factory), for the same reason ``Agent`` accepts
    both a subclassed and a fully code-first declaration.

    Immutable for the same reason ``Agent`` is: a ``Deck`` compiles this once at ``build()``.
    """

    __slots__ = ("name", "description", "state", "durable", "_graph_factory")

    # Typed alongside `__slots__` for the same reason `Agent` is: values are set via
    # `object.__setattr__` in `__init__`, which needs the annotations to type-check.
    name: str
    description: str
    state: type
    durable: bool
    _graph_factory: Callable[[], StateGraph[Any]]

    def __init__(
        self,
        *,
        base: type[WorkflowDeclaration] | None = None,
        name: str = _UNSET,
        description: str = _UNSET,
        state: type = _UNSET,
        durable: bool = _UNSET,
        graph: Callable[[], StateGraph[Any]] = _UNSET,
    ) -> None:
        source = base if base is not None else WorkflowDeclaration
        resolved_name = source.name if name is _UNSET else name
        if not resolved_name and base is not None:
            resolved_name = base.__name__
        if not resolved_name:
            raise ValueError("Workflow(name=...) is required (directly, or via base=).")
        object.__setattr__(self, "name", resolved_name)
        object.__setattr__(self, "description", source.description if description is _UNSET else description)
        resolved_state = getattr(source, "state", None) if state is _UNSET else state
        if resolved_state is None:
            raise ValueError(f"Workflow(name={resolved_name!r}) needs state=... (a pydantic model).")
        object.__setattr__(self, "state", resolved_state)
        object.__setattr__(self, "durable", source.durable if durable is _UNSET else durable)
        factory = source.build_graph if graph is _UNSET else graph
        object.__setattr__(self, "_graph_factory", factory)

    def build_graph(self) -> StateGraph[Any]:
        return self._graph_factory()

    def build(self) -> Any:
        """Compile the graph, with a checkpointer if ``durable``  -  see
        :func:`agentdeck.authoring.compile.compile_workflow`.
        """
        from agentdeck.authoring.compile import compile_workflow

        return compile_workflow(self)

    def _runner(self, **runner_options: Any) -> Any:
        from agentdeck.authoring.runners.workflow import DevWorkflowRunner

        return DevWorkflowRunner.from_workflow(self, **runner_options)

    async def run(self, state: Any = None, *, thread_id: str | None = None, **runner_options: Any) -> Any:
        """Run the graph once, direct-call (no event log)  -  see the module docstring for why.

        ``thread_id`` scopes checkpointed state (required if ``durable``). Returns the final
        state, or an :class:`~agentdeck.authoring.interrupts.InterruptResult` if a node called
        ``langgraph.types.interrupt()``  -  hand that payload to a human and resume with
        :meth:`resume`. The interrupted node re-runs from its start on resume, so it must be
        pure: put side effects in earlier nodes.
        """
        runner_options = self._thread_scoped_options(thread_id, runner_options)
        result = await self._runner(**runner_options).run(state)
        interrupted = self._interrupt_or_none(result, thread_id)
        return result if interrupted is None else interrupted

    async def run_stream(
        self, state: Any = None, *, thread_id: str | None = None, **runner_options: Any
    ) -> AsyncIterator[dict[str, Any] | InterruptResult]:
        """Streaming counterpart to :meth:`run`. Same ``thread_id`` semantics."""
        from agentdeck.authoring.interrupts import INTERRUPT_KEY

        runner_options = self._thread_scoped_options(thread_id, runner_options)
        async for event in self._runner(**runner_options).run_stream(state):
            if event["type"] == "node_update" and event["node"] == INTERRUPT_KEY:
                continue
            interrupted = self._interrupt_or_none(event["state"], thread_id) if event["type"] == "done" else None
            yield event if interrupted is None else interrupted

    def _interrupt_or_none(self, result: Any, thread_id: str | None) -> InterruptResult | None:
        from agentdeck.authoring.interrupts import as_interrupt

        interrupted = as_interrupt(result, thread_id or "")
        if interrupted is not None and not self.durable:
            raise ConfigError(
                f"{self.name} called interrupt() but is durable=False: with no checkpointer the paused run "
                "cannot be resumed. Set durable=True on the workflow.",
            )
        return interrupted

    def _thread_scoped_options(self, thread_id: str | None, runner_options: dict[str, Any]) -> dict[str, Any]:
        if self.durable and thread_id is None:
            raise ValueError(
                f"{self.name} is durable=True; a thread_id is required to load/persist checkpointed state "
                f" -  see {_WORKFLOWS_DOCS}",
            )
        if thread_id is None:
            return runner_options
        config = dict(runner_options.get("config") or {})
        config["configurable"] = {**config.get("configurable", {}), "thread_id": thread_id}
        return {**runner_options, "config": config}

    async def resume(self, thread_id: str, value: Any, **runner_options: Any) -> Any:
        """Resume the run paused on ``thread_id``: ``interrupt()`` returns ``value``."""
        from langgraph.types import Command

        if not self.durable:
            raise ConfigError(f"{self.name} is durable=False: there is no checkpointed run to resume.")
        return await self.run(Command(resume=value), thread_id=thread_id, **runner_options)

    async def pending(self) -> list[InterruptResult]:
        """Every thread of this workflow currently paused on an interrupt  -  the approval inbox."""
        from agentdeck.authoring.compile import compile_workflow
        from agentdeck.authoring.interrupts import interrupt_result

        if not self.durable:
            return []
        graph = compile_workflow(self)
        saver = graph.checkpointer
        if saver is None or isinstance(saver, bool):
            return []
        # Drain the listing first: the sqlite saver locks for the whole alist generator.
        thread_ids = {
            tid
            async for checkpoint in saver.alist(None)
            if isinstance(tid := checkpoint.config.get("configurable", {}).get("thread_id"), str)
        }
        pending: list[InterruptResult] = []
        for thread_id in sorted(thread_ids):
            snapshot = await graph.aget_state({"configurable": {"thread_id": thread_id}})
            pending.extend(interrupt_result(i.value, thread_id) for i in snapshot.interrupts)
        return pending

    def node_names(self) -> list[str]:
        """Node names on the graph excluding START/END."""
        from langgraph.graph import END, START

        return [n for n in self.build_graph().nodes if n not in {START, END}]

    def as_tool(
        self,
        *,
        name: str | None = None,
        description: str | None = None,
        output_keys: Sequence[str] | None = None,
        defaults: dict[str, Any] | None = None,
        strict_json_schema: bool = False,
    ) -> FunctionTool:
        """Expose this workflow as a :class:`agents.tool.FunctionTool`  -  used by
        :func:`agentdeck.authoring.compile.compile_agent`'s ``resolve_workflow_tool`` to turn
        an ``Agent(tools=[some_workflow])`` entry into something the SDK can call.

        ``output_keys`` filters the final state to a subset of channels. ``defaults`` pins
        specific workflow-state fields to fixed values regardless of the model's argument
        payload, and strips them from the JSON schema the model sees.
        """
        from agents.tool import FunctionTool

        from agentdeck.authoring.state import dump_state

        if not issubclass(self.state, BaseModel):
            raise TypeError(f"{self.name}.state must be a Pydantic model to be exposed as a tool; got {self.state!r}.")
        keys = tuple(output_keys) if output_keys is not None else None
        pinned = dict(defaults) if defaults else {}
        self.build_graph()  # surface graph-definition errors at tool-construction time

        async def on_invoke(_ctx: Any, raw_args: str) -> str:
            args = json.loads(raw_args) if raw_args else {}
            if pinned:
                args = {**args, **pinned}
            result = await self.run(args)
            if keys is not None:
                result: dict[str, Any] = {k: result.get(k) for k in keys}
            return dump_state(result)

        schema = self.state.model_json_schema()
        if pinned:
            schema = _strip_schema_fields(schema, pinned.keys())

        return FunctionTool(
            name=name or _CAMEL_RE.sub("_", self.name).lower(),
            description=description or self._tool_description(),
            params_json_schema=schema,
            on_invoke_tool=on_invoke,
            strict_json_schema=strict_json_schema,
        )

    def _tool_description(self) -> str:
        return self.description.strip() if self.description else f"Run the {self.name} workflow."

    def __setattr__(self, key: str, value: Any) -> None:
        raise AttributeError(f"Workflow is immutable; build a new one instead of setting {key!r}.")

    def __repr__(self) -> str:
        return f"Workflow(name={self.name!r})"


def _strip_schema_fields(schema: dict[str, Any], fields: Iterable[str]) -> dict[str, Any]:
    """Return ``schema`` with ``fields`` removed from ``properties`` and ``required``."""
    drop = set(fields)
    out = dict(schema)
    if isinstance(props := out.get("properties"), dict):
        out["properties"] = {k: v for k, v in props.items() if k not in drop}
    if isinstance(required := out.get("required"), list):
        out["required"] = [r for r in required if r not in drop]
    return out


__all__ = ["Workflow", "WorkflowDeclaration"]
