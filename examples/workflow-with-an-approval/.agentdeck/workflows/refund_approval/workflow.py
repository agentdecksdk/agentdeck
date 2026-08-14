from langgraph.graph import StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel

from agentdeck import Workflow


class RefundState(BaseModel):
    order_id: str
    amount_eur: float = 0.0
    approved: bool = False
    outcome: str = ""


def _price(state: RefundState) -> dict:
    """Work out what the refund would be. Runs before anything pauses, and only once."""
    return {"amount_eur": round(len(state.order_id) * 8.5, 2)}


def _confirm(state: RefundState) -> dict:
    decision = interrupt({"question": f"Refund EUR {state.amount_eur} on order {state.order_id}?"})
    if decision not in {"yes", "no"}:
        raise ValueError(f"approval expects 'yes' or 'no', got {decision!r}")
    return {"approved": decision == "yes"}


def _settle(state: RefundState) -> dict:
    # The node that would actually move money — after the decision, never before it, because
    # `_confirm` re-runs from its start when the answer arrives.
    return {"outcome": "refunded" if state.approved else "declined"}


def _build_graph() -> StateGraph:
    graph = StateGraph(RefundState)
    graph.add_node("price", _price)
    graph.add_node("confirm", _confirm)
    graph.add_node("settle", _settle)
    graph.set_entry_point("price")
    graph.add_edge("price", "confirm")
    graph.add_edge("confirm", "settle")
    return graph


refund_approval = Workflow(name="RefundApproval", state=RefundState, durable=True, graph=_build_graph)
