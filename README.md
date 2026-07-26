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
├── greeter/agent.py            # a BaseAgent subclass
├── new_booking/workflow.py     # a BaseWorkflow subclass
└── skills/parse-request/       # skill bundles: SKILL.md + scripts/run.py
```

No `__init__.py`, no registration — dirs are discovered by convention
(`<bundle>/agent.py`, `<bundle>/workflow.py`, `skills/*/SKILL.md`).

```python
from agentdeck import App

app = App()          # mounts ./.agentdeck
app.load()           # imports, builds, and compiles everything; fails fast

result = await app.run_agent("Greeter", "hello")                  # one-shot
state  = await app.run_workflow("NewBooking", {"request": "..."}) # one ainvoke
turn   = await app.chat("Greeter", session_id="wa-123", message="hi")  # multi-turn
```

`chat()` keeps history per `session_id` — Redis when
`AGENTDECK_SESSION_REDIS_URL` is set, in-process SQLite otherwise.

For anything long-running — and for every deployment using Redis sessions or
MCP servers — use `App.open()` instead of a bare `App()`. It runs `load()`,
starts the MCP lifecycle, and guarantees `aclose()` on exit (even on error), so
the Redis client and MCP servers are never leaked:

```python
async with App.open() as app:
    turn = await app.chat("Greeter", session_id="wa-123", message="hi")
```

`agentdeck-serve` does exactly this in its FastAPI lifespan.

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
| `POST /workflows/{name}` | JSON state in → final state out |

## Configuration

Layered pydantic-settings: process env / `.env` / YAML
(`agentdeck/runtime/config.default.yaml`). Key vars: `OPENAI_API_KEY`,
`OPENAI_BASE_URL`, `OPENAI_MODEL`, `AGENTDECK_RUNNER_*`,
`AGENTDECK_SESSION_*`, `AGENTDECK_SHELL_*`, `AGENTDECK_MCP_SERVERS`.
See `agentdeck/runtime/settings.py`.

## Development

```bash
make test      # pytest
make lint      # ruff check
make build     # sdist + wheel into dist/
```

Framework internals are documented in [`agentdeck/README.md`](agentdeck/README.md).
