<div align="center">

<img src="docs/brand/logo-blue.svg" alt="AgentDeck" width="76">

# AgentDeck SDK

**Compose. Observe. Ship.**

The production runtime for agents you already have.

<!-- The blue variant, not logo.svg: that one is fill="currentColor" for inlining, and an SVG
     loaded through <img> has nothing to inherit from, so it renders black — invisible on
     GitHub's dark theme. See docs/brand/README.md. -->

[![CI](https://img.shields.io/github/actions/workflow/status/sagi5060/agentdeck/ci.yml?branch=dev&label=CI&labelColor=0B1220&color=2563FF)](https://github.com/sagi5060/agentdeck/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/sagi5060/agentdeck?label=release&labelColor=0B1220&color=2563FF)](https://github.com/sagi5060/agentdeck/releases)
[![Python](https://img.shields.io/badge/python-3.12+-2563FF?labelColor=0B1220)](pyproject.toml)
[![License](https://img.shields.io/badge/license-MIT-2563FF?labelColor=0B1220)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-agentdecksdk-2563FF?labelColor=0B1220)](https://sagi5060.github.io/agentdeck/)

</div>

**A harness for agents you have to operate.** You write agents, workflows and skills as small
Python declarations in a `.agentdeck/` directory. AgentDeck supplies everything around them —
discovery, layered settings, provider wiring, sessions, streaming, MCP servers, typed workflows
with human approval, an HTTP surface, and one ordered event log for every run — and leaves the
running of a turn to the engines underneath.

That division is the whole design. **AgentDeck owns configuration; the
[OpenAI Agents SDK](https://github.com/openai/openai-agents-python) and
[LangGraph](https://langchain-ai.github.io/langgraph/) own execution.** There is no agent loop
here, no graph engine, and no reimplementation of either — an `Agent` compiles to an SDK agent, a
`Workflow` compiles to a LangGraph graph, and both are run by their own engine. What AgentDeck
adds is the part those libraries deliberately leave to you: where definitions live, how they are
configured, and what you can see and do while a run is in flight.

## Who it is for

You want this if you are putting agents somewhere they have to keep working: several agents and
workflows in one project, a chat surface and a batch path over the same definitions, runs you
need to inspect afterwards, approvals that outlive the process that asked for them.

You do not want this if you are writing one script that calls one model — use the Agents SDK
directly, and come back when the wiring around it has become the work. You also do not want it
if you have already built your own harness: AgentDeck is opinionated about project layout and
configuration, and those opinions are the product.

## What it deliberately does not do

- **No DSL.** Definitions are Python. There is no YAML agent format, and there will not be one.
- **No execution engine of its own.** Bugs in the agent loop or in graph execution belong
  upstream, and improvements there arrive without agentdeck doing anything.
- **No sandbox.** Tools, skills and workflow nodes are ordinary Python in your process, and a
  model-chosen tool call is trusted by design. See [SECURITY.md](SECURITY.md) before you give an
  agent something destructive.
- **No auth, no multi-tenancy, no hosted control plane, no marketplace.** `namespace` labels a
  run; it does not authenticate anyone. Put a real gateway in front of the HTTP surface.
- **No model routing, evaluation framework, or prompt management.** One OpenAI-compatible
  endpoint per process, configured by environment.

## Install

```bash
uv venv && source .venv/bin/activate
uv pip install "agentdeck[serve] @ git+https://github.com/sagi5060/agentdeck.git@v3.0.1"
export OPENAI_MODEL=gpt-4.1-mini OPENAI_API_KEY=sk-...
```

Not on PyPI yet — install from git at a tag. `OPENAI_BASE_URL` points it at any
OpenAI-compatible endpoint instead (a gateway, vLLM, Ollama). Extras: `serve` for the HTTP
surface, `durability` for the Postgres/SQLite stores, `observability` for Langfuse tracing.

Contributing to agentdeck itself is a different setup — see
[CONTRIBUTING.md](CONTRIBUTING.md).

## The smallest real thing

Everything you define lives in a `.agentdeck/` directory next to where you run. The path *is*
the registration: no catalog file, no `__init__.py`, no decorator to remember.

```text
.agentdeck/
├── agents/greeter/agent.py            # an Agent(...)
├── workflows/new_booking/workflow.py  # a Workflow(...)
└── skills/parse-request/              # SKILL.md + optional scripts
```

One file is a complete agent:

```python
# .agentdeck/agents/greeter/agent.py
from agentdeck import Agent

greeter = Agent(name="Greeter", instructions="You are a friendly scheduling assistant.")
```

`Deck` discovers, compiles and validates all of it, then runs it:

```python
import asyncio

from agentdeck import Deck


async def main() -> None:
    async with Deck.from_project() as deck:          # discovers ./.agentdeck, fails fast
        result = await deck.run("Greeter", "hello")
        print(result.output)

        turn = await deck.run("Greeter", "hi", session_id="wa-123")   # remembers across calls
        async for event in deck.stream("Greeter", "and then?", session_id="wa-123"):
            print(event.kind)                        # text.delta … run.completed


asyncio.run(main())
```

Two runnable projects — a chat agent with a tool, and a workflow that pauses for a human
approval — are in [`examples/`](examples/). Both are built by the test suite, so neither can
quietly stop working.

## What you get around your definitions

- **Sessions** — `session_id=` keeps a conversation across calls and across surfaces, in memory
  or in Redis.
- **One event log per run** — every turn, however it was started, appends to the same ordered
  log: text deltas, tool calls, token usage, the result. Status is folded from it, not stored.
- **Run control** — a run in flight can be paused, resumed or cancelled by id, at documented
  safe points, from another process.
- **Human approval** — a `durable=True` workflow node calls `interrupt()`, the run parks, and
  `deck.pending()` / `deck.answer()` finish it later, possibly somewhere else.
- **An HTTP surface** — `agentdeck-serve` puts chat, SSE streaming, workflows, and the approval
  inbox behind FastAPI without any code of yours.
- **Tools, skills and MCP** — SDK tools as plain functions, skills as `SKILL.md` directories,
  and named MCP servers from a `.mcp.json` beside your project.

## Documentation

The full docs are at **[sagi5060.github.io/agentdeck](https://sagi5060.github.io/agentdeck/)**:

- [Getting Started](https://sagi5060.github.io/agentdeck/getting-started) — install, configure,
  first agent
- [Core Concepts](https://sagi5060.github.io/agentdeck/concepts) — agents, workflows, skills,
  the event log, run control
- [Choosing a Store Backend](https://sagi5060.github.io/agentdeck/concepts/choosing-a-store-backend)
  — what to set before you deploy anything
- [Reference](https://sagi5060.github.io/agentdeck/reference) — every setting and every `Deck`
  method, generated from the code

## Project

AgentDeck is beta software under active development; breaking changes are listed in
[CHANGELOG.md](CHANGELOG.md).

- **Contributing** — [CONTRIBUTING.md](CONTRIBUTING.md). PRs target `dev`; `make check` is the
  gate. Framework internals are laid out in [`agentdeck/README.md`](agentdeck/README.md).
- **Brand** — [`docs/brand/`](docs/brand/).
- **Security** — [SECURITY.md](SECURITY.md), including what is deliberately out of scope.
- **Code of conduct** — [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
- **License** — MIT, see [LICENSE](LICENSE).
