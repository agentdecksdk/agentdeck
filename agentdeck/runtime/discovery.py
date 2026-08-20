"""Discovery: the ``.agentdeck/`` project dir (or a code-first list) becomes the invocables a
Runtime can run.

One registry for every shape a project authors  -  an agent bundle and a workflow bundle
both come out as an ``InvocableSpec``, so the Runtime is handed one mapping and never
learns which shape a name was authored in. Skills stay out: no executor plays a ``SKILL.md``
bundle, so a spec for one could only fail at the moment somebody ran it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from agentdeck.authoring.agent import Agent
from agentdeck.authoring.compile import compile_agent, link_handoffs
from agentdeck.authoring.graphs import bridge_context_nodes
from agentdeck.authoring.workflow import Workflow
from agentdeck.core.invocable import InvocableKind, InvocableSpec
from agentdeck.errors import ConfigError
from agentdeck.runtime.registry import PluginRegistry, mount_project_dir

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from agents import Agent as SDKAgent
    from agents.tool import FunctionTool

    from agentdeck.core.ports import Executor

# Which executor plays which bundle shape: a bundle names no executor of its own, the shape it
# was authored in decides. Written as strings rather than read off the adapters, because an
# adapter import here would invert the direction the Runtime's wiring depends on; a test
# pins each literal to its adapter's own ``executor`` so the two can't drift.
# ponytail: one executor per kind, forever  -  the day a second executor plays one shape, the
# executor belongs on the spec (authored per bundle), not in this table.
EXECUTOR_FOR_KIND: Final[Mapping[InvocableKind, str]] = {
    InvocableKind.AGENT: "openai-agents",
    InvocableKind.WORKFLOW: "langgraph",
}

# Where a workflow's opt-in durability travels to the executor that acts on it: the langgraph
# adapter reads ``spec.metadata[DURABLE_KEY]`` to decide whether to resolve the configured
# checkpointer at all. Spelled out rather than imported, for the reason above; the same test
# that pins the executor names pins this one to the adapter's own constant.
DURABLE_KEY: Final[str] = "durable"


def _wrapped(exc: Exception) -> type[ConfigError]:
    """The class a bundle failure is re-raised as: the original's, when it is already one of
    ours, so a ``ContextTypeError`` does not reach the caller flattened into its supertype.
    """
    return type(exc) if isinstance(exc, ConfigError) else ConfigError


class InvocableRegistry:
    """The one registry of what a project can run  -  from ``.agentdeck/`` bundles, or from a
    code-first ``agents=``/``workflows=`` list a :class:`~agentdeck.Deck` already holds.

    Construct it with the executors the Runtime was given; :meth:`load` then returns the
    mapping the Runtime takes, and raises instead if the project asks for an executor nobody
    registered  -  a wiring mistake belongs at startup, not in the middle of a run.

    An entry may be a live ``Executor`` or its bare name string  -  ``Deck.build()``
    validates which executors a catalog needs before it is safe to construct any of them (no
    network I/O), so it names them instead of building disposable instances just to ask.
    """

    def __init__(self, executors: Sequence[Executor | str]) -> None:
        self._executors = frozenset(e if isinstance(e, str) else e.name for e in executors)

    def load(
        self,
        *,
        agents: Sequence[Agent] | None = None,
        workflows: Sequence[Workflow] | None = None,
        resolve_skills: Callable[[Sequence[str]], tuple[str, Sequence[FunctionTool]]] | None = None,
        resolve_workflow_tool: Callable[[Workflow], FunctionTool] | None = None,
        bundle_of: Mapping[str, str] | None = None,
        context_type: object | None = None,
    ) -> Mapping[str, InvocableSpec]:
        """Compile every agent and workflow to an ``InvocableSpec``.

        ``agents``/``workflows`` default to a discovery scan of ``./.agentdeck`` (one
        ``Agent``/``Workflow`` instance per bundle module); pass explicit sequences for a
        code-first catalog instead  -  ``Deck.from_project()`` and ``Deck(agents=..., ...)``
        both end up here, so the two build the same way. ``resolve_skills``/
        ``resolve_workflow_tool`` are the catalog-aware hooks ``compile_agent`` needs for
        ``skills=``/a workflow used as a tool; a bare discovery scan passes neither, so an
        agent declaring either fails loudly rather than compiling silently short. ``bundle_of``
        names the bundle a discovered ``agents``/``workflows`` entry came from (name -> source
        path); a caller that already ran its own scan (``Deck.from_project``) supplies it since
        the association is otherwise lost the moment ``agents``/``workflows`` are handed in as
        plain instances  -  a code-first entry has no bundle, so it is simply absent here.
        ``context_type`` is the owning deck's ``Deck(context=...)`` declaration, checked against
        every ``ToolCtx[...]`` requirement in the catalog as each entry compiles.

        Eager on purpose: a bundle that can't be imported, an agent that can't be built and
        an executor that isn't registered all fail here, not mid-conversation.
        """
        bundle_of = dict(bundle_of) if bundle_of else {}
        if agents is None:
            registry = self._discover(Agent, type_dir="agents", module_name="agent", label="agent")
            agents = list(registry.list().values())
            bundle_of.update(registry.bundle_files())
        if workflows is None:
            registry = self._discover(Workflow, type_dir="workflows", module_name="workflow", label="workflow")
            workflows = list(registry.list().values())
            bundle_of.update(registry.bundle_files())
        specs: dict[str, InvocableSpec] = {}
        compiled: dict[str, SDKAgent] = {}
        for agent in agents:
            try:
                compiled[agent.name] = compile_agent(
                    agent,
                    resolve_skills=resolve_skills,
                    resolve_workflow_tool=resolve_workflow_tool,
                    context_type=context_type,
                )
            except Exception as exc:
                bundle_file = bundle_of.get(agent.name)
                if bundle_file is None:  # code-first: no bundle to name, so the raw exception stands
                    raise
                raise _wrapped(exc)(f"{bundle_file} failed to build: {exc}") from exc
        link_handoffs(compiled, agents)
        for agent in agents:
            self._add(specs, agent.name, InvocableKind.AGENT, compiled[agent.name])
        for workflow in workflows:
            try:
                # uncompiled: the langgraph adapter compiles the graph itself, around the
                # checkpointer  -  ``durable`` names, which is why that flag travels with the
                # spec rather than staying on the Workflow only the authoring layer can see.
                # Bridged here rather than in the adapter so a node declaring two
                # ``ToolCtx[...]`` parameters fails at build(), exactly where a tool's would.
                graph = bridge_context_nodes(workflow.build_graph(), context_type=context_type)
            except Exception as exc:
                bundle_file = bundle_of.get(workflow.name)
                if bundle_file is None:
                    raise
                raise _wrapped(exc)(f"{bundle_file} failed to build: {exc}") from exc
            self._add(specs, workflow.name, InvocableKind.WORKFLOW, graph, metadata={DURABLE_KEY: workflow.durable})
        return specs

    def _discover(self, base_class: type, *, type_dir: str, module_name: str, label: str) -> PluginRegistry[Any]:
        package = mount_project_dir()
        registry = PluginRegistry(
            package, base_class=base_class, module_name=module_name, type_dir=type_dir, label=label
        )
        registry.list(refresh=True)
        return registry

    def _add(
        self,
        specs: dict[str, InvocableSpec],
        name: str,
        kind: InvocableKind,
        native: Any,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        # Only catches a collision across kinds; a collision within one kind (two bundles
        # exposing the same invocable name) already raised inside the scan that fed this.
        if name in specs:
            raise ConfigError(
                f"an agent and a workflow are both named {name!r} (kinds: {specs[name].kind.value} and "
                f"{kind.value}); one name is one invocable  -  rename one of them."
            )
        executor = EXECUTOR_FOR_KIND[kind]
        if executor not in self._executors:
            raise ConfigError(
                f"{kind.value} {name!r} needs executor {executor!r}, which is not registered. "
                f"Registered: {sorted(self._executors)}."
            )
        specs[name] = InvocableSpec(name=name, kind=kind, executor=executor, native=native, metadata=metadata or {})


__all__ = ["DURABLE_KEY", "EXECUTOR_FOR_KIND", "InvocableRegistry"]
