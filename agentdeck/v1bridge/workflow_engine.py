"""The engine v1's workflow surface runs on: an ``EnginePort`` that plays a graph exactly
the way ``workflows/runners/dev.py`` plays it.

The M0 langgraph engine compiles ``spec.native`` around its own checkpointer and streams it
bare, which is right for a v2 caller and wrong for a v1 one. v1 runs a workflow inside two
things the graph itself cannot supply: one shared :class:`Workspace` (``FileNode`` and
``SkillNode`` call ``Workspace.require()``, so without it they raise) and one Langfuse
observation every node's agent and skill span nests under. And v1's own
``BaseWorkflow.build()`` is what decides a graph's checkpointer — the configured one for
``durable = True``, none at all otherwise. All three are v1's, so they are *reused* here
rather than reimplemented: this class calls v1's runner for its resolved configuration and
v1's compiled graph, and keeps the M0 engine's stream translation.

That also settles where the configured checkpointer is resolved: nowhere until a durable
workflow actually runs one. A composition root registering this engine resolves nothing, so
the ``[durability]`` extra stays optional for a project that only chats, while a durable
workflow gets the real checkpointer because v1's ``build()`` gave its graph one.
"""

from __future__ import annotations

from contextlib import aclosing
from typing import TYPE_CHECKING, Any

from agentdeck.adapters.engines.langgraph.engine import LangGraphEngine
from agentdeck.core.content import DataBlock
from agentdeck.core.events import RunCompleted, RunInterrupted
from agentdeck.errors import ConfigError
from agentdeck.runtime.observability import trace_run
from agentdeck.runtime.workspace import Workspace, current_capture
from agentdeck.workflows.nodes import STREAM_CONFIGURABLE_KEY
from agentdeck.workflows.runners.dev import DevWorkflowRunner
from agentdeck.workflows.state import json_default

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable

    from langchain_core.runnables import RunnableConfig

    from agentdeck.core.context import RunContext
    from agentdeck.core.events import KnownPayload
    from agentdeck.core.invocable import InvocableSpec
    from agentdeck.workflows.base import BaseWorkflow


class V1CompatWorkflowEngine(LangGraphEngine):
    """Runs a workflow with v1's compiled graph, sandbox scope and trace span.

    ``workflow_for`` is v1's own workflow lookup (``App.workflows.get``), taken rather than
    rebuilt so a graph is compiled once per process and around the checkpointer v1 chose for
    it. Without an injected lookup this falls back to the M0 engine's own behavior, so a
    code-first caller still runs a ``StateGraph`` handed to it on a spec.
    """

    def __init__(self, workflow_for: Callable[[str], type[BaseWorkflow]] | None = None) -> None:
        super().__init__()
        self._workflow_for = workflow_for

    def _thread_id(self, ctx: RunContext) -> str:
        """v1's caller names the langgraph thread and keeps resuming it, so the thread is the
        session rather than this one run: ``POST /workflows/X?thread_id=t`` then
        ``POST /workflows/X/t/resume`` is two runs on one thread, which ``ctx.run_id`` could
        not express. A run with no thread (a non-durable workflow) keeps its own."""
        return ctx.session_id or ctx.run_id

    def _leaf(self, value: Any) -> Any:
        """v1's own JSON encoder, so a pydantic or dataclass leaf in a node's update reaches
        the wire as the object v1 showed rather than as its ``repr``."""
        return json_default(value)

    async def _drive(
        self,
        spec: InvocableSpec,
        graph_input: Any,
        thread_id: str,
        ctx: RunContext,
    ) -> AsyncGenerator[KnownPayload, None]:
        if self._workflow_for is None:
            async with aclosing(super()._drive(spec, graph_input, thread_id, ctx)) as stream:
                async for payload in stream:
                    yield payload
            return
        workflow = self._workflow_for(spec.name)
        if workflow.durable and ctx.session_id is None:
            # v1's guard, with v1's message: a durable graph loads and persists its state by
            # thread, so running one under a thread nobody can name back is a lost run.
            raise ValueError(
                f"{workflow.__name__} is durable=True; a thread_id is required to load/persist checkpointed state.",
            )
        # v1's runner is what resolves the sandbox environment and starts Langfuse; the graph
        # it carries is v1's own compiled one, checkpointer included.
        runner = DevWorkflowRunner.from_workflow(workflow)
        config: RunnableConfig = {
            **runner.config,
            # The stream key tells a nested AgentNode it may stream, which is how its deltas
            # reach the custom stream. Always on, unlike v1: one run produces one canonical
            # stream, and what the caller does with it is not the engine's business.
            "configurable": {
                **(runner.config.get("configurable") or {}),
                "thread_id": thread_id,
                STREAM_CONFIGURABLE_KEY: True,
            },
        }
        with trace_run(current_capture(), name=workflow.name or workflow.__name__, input=graph_input) as tr:
            reported = False
            # The workspace is a ContextVar scope, so the stream it wraps has to be closed
            # from inside it — an abandoned generator releases it from the wrong context.
            async with (
                Workspace.open(environment=runner.environment, input_files=runner.input_files),
                aclosing(self._play(runner.graph, graph_input, config)) as stream,
            ):
                try:
                    async for payload in stream:
                        if isinstance(payload, RunInterrupted) and not workflow.durable:
                            raise ConfigError(
                                f"{workflow.__name__} called interrupt() but is durable=False: with no checkpointer "
                                "the paused run cannot be resumed. Set `durable = True` on the workflow.",
                            )
                        produced = _reported(payload)
                        if produced is not None:
                            tr.set_output(produced)
                            reported = True
                        yield payload
                except GeneratorExit:
                    # How a *successful* run ends too: the Runtime stops reading at the terminal
                    # event, closing this generator. Only a run that reported nothing was abandoned.
                    if not reported:
                        tr.set_output(error="GeneratorExit: run did not reach its terminal event")
                    raise
                except BaseException as exc:
                    tr.set_output(error=f"{type(exc).__name__}: {exc}")
                    raise


def _reported(payload: KnownPayload) -> Any:
    """What this payload tells the trace the run produced, or ``None`` if it says nothing —
    v1 set the observation's output from the graph's final state, and a paused run's answer
    to "what did it produce" is the question it is waiting on."""
    if isinstance(payload, RunCompleted):
        return next((block.data for block in payload.output if isinstance(block, DataBlock)), None)
    if isinstance(payload, RunInterrupted):
        return payload.payload
    return None


__all__ = ["V1CompatWorkflowEngine"]
