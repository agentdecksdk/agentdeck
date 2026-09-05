# Chat agent with a tool

An agent that answers questions about orders, and a plain Python function it calls to look one up.
Six lines of declaration: no JSON schema, no call parsing, no tool loop.
[Source](https://github.com/agentdecksdk/agentdeck/tree/main/examples/chat-agent-with-a-tool).

```text
.agentdeck/
└── agents/order_desk/agent.py    # @tool + Agent(name="OrderDesk", tools=[...])
run.py                            # deck.run("OrderDesk", "where is order A-1001?")
```

The file's location is the registration. Nothing imports `agent.py`, and there is no catalog file
to add it to.

## The declaration

```python no-test reason="the example's own file, discovered from .agentdeck/ rather than imported"
from agentdeck import Agent, tool

_ORDERS = {"A-1001": "shipped, arriving Thursday"}


@tool
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
```

## Run it

```bash
uv pip install agentdeck-sdk
export OPENAI_MODEL=gpt-4.1-mini OPENAI_API_KEY=sk-...
python run.py
```

Run it from the example's own directory: `Deck.from_project()` discovers `./.agentdeck`, so the
working directory picks the project. `OPENAI_BASE_URL` points it at any OpenAI-compatible server
instead, and Chat-Completions-only servers also want `OPENAI_USE_RESPONSES=false`.

## What to look at

- The parameter schema comes off the type hints and the description off the docstring;
  `build()` compiles the function into the tool the model is offered. A sync body runs on a worker
  thread rather than the event loop.
- The instructions telling the model to call the tool are the whole contract. No rule engine
  enforces it.
- The tool runs in this process with these privileges, on arguments the model chose. Keep anything
  destructive behind [human approval](/runs-and-control/human-input) rather than an agent's
  judgement.

Next: [Tools](/build-your-deck/tools) · [Agents](/build-your-deck/agents)
