"""The langgraph adapter's cases for the shared contract suite (#53).

Appended onto ``contract_cases.CASES`` — same invariants, a real ``StateGraph`` instead of
the stub or an ``agents.Agent``. Every node here ignores whatever checkpoint history a
previous test function left behind on the same ``thread_id`` (fresh dict input replaces
state channels, no accumulation) — the same "safe to share one engine across every test
function" property ``openai_agents_cases.TailScriptedModel`` documents, needed here for
the same reason (``test_event_stream.py`` reuses one ``Case`` — and its engine — across
many test functions with the same ``ctx.run_id``).
"""

from __future__ import annotations

from typing import Any

from case_types import Case
from langgraph.graph import END, START, StateGraph

from agentdeck.adapters.engines.langgraph import LangGraphEngine
from agentdeck.core.invocable import InvocableKind, InvocableSpec


def _node_a(_state: dict[str, Any]) -> dict[str, Any]:
    return {"out": "done"}


def _boom(_state: dict[str, Any]) -> dict[str, Any]:
    raise RuntimeError("engine blew up")


def _interrupts(_state: dict[str, Any]) -> dict[str, Any]:
    from langgraph.types import interrupt

    value = interrupt({"reason": "approval", "question": "approve?"})
    return {"decision": value}


def _graph(*nodes: tuple[str, Any]) -> StateGraph[Any]:
    g: StateGraph[Any] = StateGraph(dict)
    names = [name for name, _ in nodes]
    for name, fn in nodes:
        g.add_node(name, fn)
    g.add_edge(START, names[0])
    for a, b in zip(names, names[1:], strict=False):
        g.add_edge(a, b)
    g.add_edge(names[-1], END)
    return g


def _spec(name: str, graph: StateGraph[Any]) -> InvocableSpec:
    return InvocableSpec(name=name, kind=InvocableKind.WORKFLOW, engine=LangGraphEngine.engine, native=graph)


def langgraph_cases() -> list[Case]:
    return [
        Case(
            id="langgraph/completes",
            engine=LangGraphEngine(),
            spec=_spec("Grapher", _graph(("node_a", _node_a))),
            ends="terminal",
        ),
        Case(
            id="langgraph/interrupts",
            engine=LangGraphEngine(),
            spec=_spec("Approver", _graph(("gate", _interrupts))),
            ends="suspended",
        ),
        Case(
            id="langgraph/raises-midstream",
            engine=LangGraphEngine(),
            spec=_spec("Boom", _graph(("boom", _boom))),
            ends="terminal",
        ),
    ]


__all__ = ["langgraph_cases"]
