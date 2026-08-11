# agentdeck

Declarative harness over the [OpenAI Agents SDK](https://github.com/openai/openai-agents-python)
and [LangGraph](https://langchain-ai.github.io/langgraph/). agentdeck owns
**configuration** — settings, capabilities, skills, runners, graph compilation,
plug-in discovery. Execution stays in the SDK / LangGraph.

## Install

```bash
git clone https://github.com/sagi5060/agentdeck.git
cd agentdeck
uv venv
uv pip install -e ".[dev,serve]"     # or: make install
```

## Quick start

Everything you define lives in a `.agentdeck/` directory next to where you run:

```text
.agentdeck/
├── agents/greeter/agent.py          # an Agent(...)
├── workflows/new_booking/workflow.py  # a Workflow(...)
└── skills/parse-request/            # skill bundles: SKILL.md
```

No `__init__.py`, no registration — dirs are discovered by convention
(`agents/<bundle>/agent.py`, `workflows/<bundle>/workflow.py`, `skills/*/SKILL.md`).

```python
from agentdeck import Deck

async with Deck.from_project() as deck:  # discovers and compiles ./.agentdeck; fails fast
    result = await deck.run("Greeter", "hello")                          # one-shot
    state = await deck.run("NewBooking", {"request": "..."})            # one workflow run
    turn = await deck.run("Greeter", "hi", session_id="wa-123")         # multi-turn

    async for event in deck.stream("Greeter", "hi", session_id="wa-123"):  # streamed turn
        ...  # the run's own canonical Events: text.delta per chunk, run.completed last
```

A `durable=True` workflow can pause for a human: a node calls
`interrupt(payload)`, `run` returns
`{"type": "interrupt", "payload": ..., "thread_id": ...}`, and the decision comes
back later — possibly in another process — via `deck.pending()` (the approval
inbox, listed by `run_id`) followed by `deck.answer(run_id, value)`. The
interrupted node re-runs from its start on resume, so keep it pure and put side
effects in earlier nodes.

`session_id=` keeps history across calls — Redis when
`AGENTDECK_SESSION` is set, in-process SQLite otherwise.

`async with Deck.from_project() as deck:` starts the MCP lifecycle and
guarantees `aclose()` on exit (even on error), so the Redis client and MCP
servers are never leaked. `agentdeck-serve` does exactly this in its FastAPI
lifespan.

## Serve

```bash
agentdeck-serve                  # FastAPI on :8000 (needs the [serve] extra)
# or with Redis-backed sessions:
docker compose up
```

| Endpoint | Does |
|---|---|
| `GET /health` | inventory of loaded agents / workflows / skills |
| `POST /agents/{name}/chat` | `{"session_id", "message"}` → `{"output"}` |
| `POST /agents/{name}/chat?stream=true` | same body → SSE: `delta` frames, then one `done` frame with `{"output", "usage"}` (or an `error` frame if the turn fails) |
| `POST /workflows/{name}` | JSON state in → final state out (optional `?thread_id=` for durable runs) |
| `POST /workflows/{name}?stream=true` | SSE: `node_update`/`custom` frames, then one `done` frame with the final state — or one `interrupt` frame if the run paused |
| `GET /workflows/{name}/pending` | threads paused on a human decision — the approval inbox |
| `POST /workflows/{name}/{thread_id}/resume` | `{"value": ...}` → final state, or the next interrupt |

Every endpoint above is served by the `Runtime` the `Deck` composes, so every
turn — chat or workflow — is recorded as one canonical event log; the frames
above are unchanged.

## Configuration

Layered pydantic-settings: process env / `.env` / YAML
(`agentdeck/runtime/config.default.yaml`). Key vars: `OPENAI_API_KEY`,
`OPENAI_BASE_URL`, `OPENAI_MODEL`, `AGENTDECK_RUNNER_*`,
`AGENTDECK_SESSION`,
`AGENTDECK_EVENTS` (where the event log goes — the URL's scheme names the backend:
`memory://` by default, or `sqlite://<path>` to keep it across restarts). Named MCP
servers go in a `.mcp.json` file at the project root (a sibling of `.agentdeck/`), not
an env var. See `agentdeck/runtime/settings.py`.

## Development

```bash
make test      # pytest
make lint      # ruff check
make build     # sdist + wheel into dist/
```

Framework internals are documented in [`agentdeck/README.md`](agentdeck/README.md).
