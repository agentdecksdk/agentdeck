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
from agentdeck.authoring.native import NativeDefinition
from agentdeck.core.invocable import AgentInstance, InvocableKind, InvocableSpec
from agentdeck.errors import ConfigError
from agentdeck.runtime.registry import PluginRegistry, mount_project_dir

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from agents import Agent as SDKAgent
    from agents.tool import FunctionTool

    from agentdeck.authoring.compile import Delegate
    from agentdeck.core.ports import Executor
    from agentdeck.core.workers import SyncToolWorkers

# Which executor plays which bundle shape: a bundle names no executor of its own, the shape it
# was authored in decides. Written as strings rather than read off the adapters, because an
# adapter import here would invert the direction the Runtime's wiring depends on; a test
# pins each literal to its adapter's own ``executor`` so the two can't drift.
# ponytail: one executor per kind, forever  -  the day a second executor plays one shape, the
# executor belongs on the spec (authored per bundle), not in this table.
EXECUTOR_FOR_KIND: Final[Mapping[InvocableKind, str]] = {
    InvocableKind.AGENT: "openai-agents",
}

NATIVE_EXECUTOR: Final[str] = "native"
"""What plays an AgentDeck-native definition, whatever kind it is  -  the one shape that names its
own executor rather than inheriting one from the table above."""


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
        workflows: Sequence[NativeDefinition] | None = None,
        resolve_skills: Callable[[Sequence[str]], tuple[str, Sequence[FunctionTool]]] | None = None,
        bundle_of: Mapping[str, str] | None = None,
        context_type: object | None = None,
        delegate: Delegate | None = None,
        workers: SyncToolWorkers | None = None,
    ) -> Mapping[str, InvocableSpec]:
        """Compile every agent and workflow to an ``InvocableSpec``.

        ``agents``/``workflows`` default to a discovery scan of ``./.agentdeck`` (one ``Agent``
        instance or ``@workflow`` definition per bundle module); pass explicit sequences for a
        code-first catalog instead  -  ``Deck.from_project()`` and ``Deck(agents=..., ...)``
        both end up here, so the two build the same way. ``resolve_skills`` is the catalog-aware
        hook ``compile_agent`` needs for ``skills=``; a bare discovery scan passes none, so an
        agent declaring skills fails loudly rather than compiling silently short. ``bundle_of``
        names the bundle a discovered ``agents``/``workflows`` entry came from (name -> source
        path); a caller that already ran its own scan (``Deck.from_project``) supplies it since
        the association is otherwise lost the moment ``agents``/``workflows`` are handed in as
        plain instances  -  a code-first entry has no bundle, so it is simply absent here.
        ``context_type`` is the owning deck's ``Deck(context=...)`` declaration, checked against
        every ``ToolCtx[...]`` requirement in the catalog as each entry compiles. ``delegate`` is
        how a compiled ``subagents=`` tool starts its child run; without one an agent declaring
        subagents fails here rather than compiling a tool that could only fail when the model
        reached for it.

        Eager on purpose: a bundle that can't be imported, an agent that can't be built and
        an executor that isn't registered all fail here, not mid-conversation.
        """
        bundle_of = dict(bundle_of) if bundle_of else {}
        if agents is None:
            registry = self._discover(Agent, type_dir="agents", module_name="agent", label="agent")
            agents = list(registry.list().values())
            bundle_of.update(registry.bundle_files())
        if workflows is None:
            registry = self._discover(
                NativeDefinition,
                type_dir="workflows",
                module_name="workflow",
                label="workflow",
                kind=InvocableKind.WORKFLOW,
            )
            workflows = list(registry.list().values())
            bundle_of.update(registry.bundle_files())
        specs: dict[str, InvocableSpec] = {}
        compiled: dict[str, SDKAgent] = {}
        catalog = {agent.name: agent for agent in agents}
        for agent in agents:
            try:
                compiled[agent.name] = compile_agent(
                    agent,
                    resolve_skills=resolve_skills,
                    context_type=context_type,
                    catalog=catalog,
                    delegate=delegate,
                    workers=workers,
                )
            except Exception as exc:
                bundle_file = bundle_of.get(agent.name)
                if bundle_file is None:  # code-first: no bundle to name, so the raw exception stands
                    raise
                raise _wrapped(exc)(f"{bundle_file} failed to build: {exc}") from exc
        link_handoffs(compiled, agents)
        for agent in agents:
            self._add(specs, agent.name, InvocableKind.AGENT, compiled[agent.name], instance=agent)
        for workflow in workflows:
            self._add(specs, workflow.name, workflow.kind, workflow, executor=NATIVE_EXECUTOR)
        return specs

    def _discover(
        self,
        base_class: type,
        *,
        type_dir: str,
        module_name: str,
        label: str,
        kind: InvocableKind | None = None,
    ) -> PluginRegistry[Any]:
        package = mount_project_dir()
        registry = PluginRegistry(
            package, base_class=base_class, module_name=module_name, type_dir=type_dir, label=label, kind=kind
        )
        registry.list(refresh=True)
        return registry

    def _add(
        self,
        specs: dict[str, InvocableSpec],
        name: str,
        kind: InvocableKind,
        native: Any,
        executor: str | None = None,
        instance: Agent | None = None,
    ) -> None:
        # Only catches a collision across kinds; a collision within one kind (two bundles
        # exposing the same invocable name) already raised inside the scan that fed this.
        if name in specs:
            raise ConfigError(
                f"{name!r} is registered under two kinds: {specs[name].kind.value} and {kind.value}; "
                "one name is one invocable  -  rename one of them."
            )
        executor = executor or EXECUTOR_FOR_KIND[kind]
        if executor not in self._executors:
            raise ConfigError(
                f"{kind.value} {name!r} needs executor {executor!r}, which is not registered. "
                f"Registered: {sorted(self._executors)}."
            )
        # The instance rides the spec because the spec is what every play resolves  -  a fresh run,
        # a lifted pause and an answered interrupt all end at ``Runtime._resolve``, so ``ctx.agent``
        # cannot be forgotten on one of them the way an argument threaded from the caller could.
        metadata = {"agent": AgentInstance(name=name, declaration=instance)} if instance is not None else {}
        specs[name] = InvocableSpec(name=name, kind=kind, executor=executor, native=native, metadata=metadata)


__all__ = ["EXECUTOR_FOR_KIND", "NATIVE_EXECUTOR", "InvocableRegistry"]
