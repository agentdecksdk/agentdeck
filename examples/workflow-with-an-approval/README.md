# Workflow with an approval

A refund that does not happen until a person says yes. Three deterministic nodes, one of which
pauses mid-graph and waits — possibly for another process, on another day.

```text
.agentdeck/
└── workflows/refund_approval/workflow.py   # typed state + a LangGraph graph, durable=True
run.py                                       # run -> interrupt -> pending() -> answer()
```

## Run it

```bash
uv venv && source .venv/bin/activate
uv pip install "agentdeck-sdk[durability]"
export OPENAI_MODEL=none OPENAI_API_KEY=none
python run.py
```

Run it from *this* directory — the working directory is what picks the project. Nothing here
calls a model, so the two `OPENAI_*` values are placeholders that only satisfy configuration;
the whole example is deterministic Python, which is exactly the point of putting the money in a
workflow rather than in an agent's hands. `[durability]` brings the SQLite checkpointer that
`durable=True` needs.

Expected output:

```text
{'type': 'interrupt', 'payload': {'question': 'Refund EUR 51.0 on order A-1003?'}, 'thread_id': 'refund-A-1003'}
{'order_id': 'A-1003', 'amount_eur': 51.0, 'approved': True, 'outcome': 'refunded'}
```

## What to look at

- **`interrupt()` returns the question instead of a final state.** The run is parked in the
  checkpointer; `pending()` is the inbox of parked runs and `answer(run_id, value)` is how one
  continues. `answer()` passes the value through unvalidated, so `_confirm` owns the answer
  contract and rejects anything other than the `"yes"` or `"no"` strings it expects.
- **The interrupting node re-runs from its start on resume.** `_confirm` therefore does nothing
  but ask, `_price` does its work before the pause, and `_settle` — the node that would actually
  move money — runs after the decision. A side effect inside `_confirm` would happen twice.
- **Answering from a *second* process needs a shared event log.** `durable=True` covers the
  graph's own state, but `pending()` reads the Runtime's event log, which defaults to in-process
  memory. Set `AGENTDECK_EVENTS` to `sqlite://`, `redis://` or `postgresql://` before expecting
  one process to see another's paused run.

Next: [Human Approval](https://agentdecksdk.com/guides/human-approval) ·
[Choosing a Store Backend](https://agentdecksdk.com/concepts/choosing-a-store-backend)
