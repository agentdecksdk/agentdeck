from agents import function_tool

from agentdeck import Agent

_ORDERS = {
    "A-1001": "shipped, arriving Thursday",
    "A-1002": "packed, leaves the warehouse tonight",
    "A-1003": "refunded on 3 March",
}


@function_tool
def order_status(order_id: str) -> str:
    """Look up the current status of one order by its id."""
    return _ORDERS.get(order_id, "no such order")


order_desk = Agent(
    name="OrderDesk",
    instructions=(
        "You are the order desk for an online shop. Call order_status before answering any "
        "question about an order, and never guess a status. Keep replies to one short sentence."
    ),
    tools=[order_status],
)
