# An existing LangGraph agent, wrapped

You already have a LangGraph graph that works. You do not want to rewrite it, port it to a
framework, or hand its execution to something else — you want the things production asks for
that a graph does not come with: an ordered event log, streaming, run control, an HTTP surface.

This example is that, and nothing else. `pipeline.py` is the graph, written without AgentDeck and
unchanged by adopting it. `workflow.py` is the entire adoption:

```python
from agentdeck import Workflow

from .pipeline import TicketState, build

triage = Workflow(name="Triage", state=TicketState, graph=build)
```

```text
.agentdeck/
└── workflows/triage/
    ├── pipeline.py     # your existing graph — zero agentdeck imports
    └── workflow.py     # the four lines that wrap it
run.py                  # run() -> final state, stream() -> the run's own events
```

## Run it

```bash
uv venv && source .venv/bin/activate
uv pip install agentdeck-sdk
export OPENAI_MODEL=none OPENAI_API_KEY=none   # no node here calls a model
python run.py
```

```text
{'input': 'the checkout API is down for everyone', 'severity': 'urgent', 'queue': 'oncall', ...}
run.started
node.updated
node.updated
node.updated
run.completed
```

The first line is the graph's own final state, identical to what `builder.compile().invoke(...)`
would have returned. The rest is what it gained: every node update, in order, on one log per run,
readable by another process through a shared event store.

## What the wrapping asks of your graph

Two things, both of which an existing graph usually already satisfies:

- **`graph=` takes an uncompiled `StateGraph` factory**, a plain `() -> StateGraph`. If your
  module ends in `graph = builder.compile()`, export the builder too. AgentDeck compiles it
  itself, which is what lets it attach a checkpointer when a workflow is `durable=True`.
- **A sibling module is imported relatively** — `from .pipeline import …`. Bundles are imported
  under AgentDeck's own module alias, so `import pipeline` will not resolve, and a module outside
  `.agentdeck/` is not on the path at all. Moving the file next to its declaration is the whole
  change; its contents stay as they were.

Your state type is not one of them. `TicketState` here is a `TypedDict`, which is what most
existing graphs use — a pydantic model works too, and neither is required.

## What you get, without touching the graph

- `deck.run("Triage", state)` — the final state, exactly as the graph returns it.
- `deck.stream(...)` — `run.started`, a `node.updated` per node, `run.completed`, as they happen.
- `deck.status(run_id)` / `pause` / `resume` / `cancel` — run control from another process.
- `agentdeck-serve` — the same workflow over HTTP and SSE, with no code of yours.

Adding `durable=True` and a `langgraph.types.interrupt()` call is the next step up: the run parks
mid-graph and a person finishes it later, possibly elsewhere. That is
[`workflow-with-an-approval`](../workflow-with-an-approval/).
