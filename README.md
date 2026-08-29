<div align="center">

<img src="docs/brand/logo-blue.svg" alt="AgentDeck" width="76">

# AgentDeck SDK

**Agentic software should feel like software.**

Build agents, tools and workflows as normal software.
AgentDeck gives them one execution model you can observe, control and extend.

<!-- The blue variant, not logo.svg: that one is fill="currentColor" for inlining, and an SVG
     loaded through <img> has nothing to inherit from, so it renders black: invisible on
     GitHub's dark theme. See docs/brand/README.md. -->

[![CI](https://img.shields.io/github/actions/workflow/status/agentdecksdk/agentdeck/ci.yml?branch=dev&label=CI&labelColor=0B1220&color=2563FF)](https://github.com/agentdecksdk/agentdeck/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/agentdecksdk/agentdeck?label=release&labelColor=0B1220&color=2563FF)](https://github.com/agentdecksdk/agentdeck/releases)
[![Python](https://img.shields.io/badge/python-3.12+-2563FF?labelColor=0B1220)](pyproject.toml)
[![License](https://img.shields.io/badge/license-MIT-2563FF?labelColor=0B1220)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-agentdecksdk-2563FF?labelColor=0B1220)](https://agentdecksdk.com/)

</div>

```bash
pip install agentdeck-sdk
```

---

## Built to stay out of your way

Agent systems accumulate infrastructure quickly, and decisions that look small early are the hardest to change later.

Execution, control, state, observability, reporting, interaction, and integration already have their place in AgentDeck, and they were designed to work together: less machinery to build today, and nothing to retrofit around your application when you need the next one.

## Simple code. Serious capabilities.

Your code stays about the behavior and structure of your application:

* **Tools do work:** A plain Python function for a context-free tool, `@tool` for one that declares `ToolCtx[T]` to receive strongly typed application data without exposing internal state to the model prompt.
* **Agents make decisions:** Declarations configuring model instructions, tools, skills, and handoffs.
* **Workflows manage process:** Ordinary async Python functions decorated with `@workflow`. Use `WorkflowCtx` to invoke targets (`ctx.invoke`), branch in parallel (`ctx.parallel`), or park for human input (`ctx.ask`).
* **Decks assemble applications:** `Deck` is the single composition root. It validates dependencies, schemas, and catalogs at startup before the first turn.

```python
from agentdeck import Agent, Deck, ToolCtx, WorkflowCtx, tool, workflow


class AppContext:
    def search(self, query: str) -> str:
        return "Internal records found."


@tool
def lookup_records(query: str, ctx: ToolCtx[AppContext]) -> str:
    """Look up internal records."""
    return ctx.data.search(query)


agent = Agent(
    name="SupportBot",
    instructions="Help users resolve inquiries using internal tools.",
    tools=[lookup_records],
)


@workflow
async def handle_request(ctx: WorkflowCtx, ticket: dict) -> str:
    """Coordinate support tasks with human approval for sensitive changes."""
    response = await ctx.invoke(agent, ticket["query"])
    if ticket.get("urgent"):
        approved = await ctx.ask(f"Approve urgent response for ticket {ticket['id']}?", options=[True, False])
        if not approved:
            return "Escalated to human supervisor."
    return response


deck = Deck(agents=[agent], workflows=[handle_request], context=AppContext)
```

## You build the behavior. AgentDeck manages the machinery.

```text
Run
├── executions     nested invocations, each addressable
├── events         one ordered log per run
├── reports        progress and status from inside the work
├── state          sessions that outlive a single call
├── interaction    branches that wait for external input
└── control        pause, resume, cancel
```

| You own | AgentDeck owns |
| --- | --- |
| agents, tools, workflows | **Events.** One ordered stream of what happened. |
| what progress means | **Reporting.** Progress and status, sent from inside the work. |
| when work should stop | **Control.** Execution paused, resumed or cancelled at safe points. |
| when a person decides | **Interaction.** Branches that wait for external input. |
| business state | **State.** Sessions that outlive a single call. |
| your UI and integrations | **Surfaces.** Observers, HTTP/SSE and your UI read the same run. |

The complexity is still there. It just lives in the layer built for it.

### Streaming and event logs

Every managed execution produces an immutable, ordered stream of events:

```python
async with deck:
    async for event in deck.stream("handle_request", {"id": "T-100", "query": "status update"}):
        print(event.kind)  # run.started, tool.call.started, text.delta, run.completed
```

### Execution you can steer

A Run is first-class and addressable. Steering methods belong to the handle:

```python
run = await deck.runs.start("handle_request", {"id": "T-100", "query": "status update", "urgent": True})

if run.can.pause:
    await run.pause()
    await run.resume()

# Answer an execution waiting on human approval:
await run.answer(True)
result = await run
```

`deck.runs.get(id)` rehydrates the handle in another process, so a run parked on input or paused by a web request can be resumed by an asynchronous worker.

---

## Where your definitions live

Everything you define can live in a `.agentdeck/` directory next to where you run. The path is the registration:

```text
.agentdeck/
├── agents/support_bot/agent.py        # an Agent(...)
├── workflows/handle_ticket/workflow.py # a @workflow function
└── skills/troubleshooting/            # SKILL.md + prompt resources
```

```python
async with Deck.from_project() as deck:
    result = await deck.run("handle_ticket", {"id": "T-101", "query": "help"})
```

`Deck.from_project()` discovers and validates all definitions at startup, catching missing skills, invalid types, or broken references at `build()` time.

---

## Who it is for

You want this if you are putting agents somewhere they have to keep working: several agents and workflows in one project, a chat surface and a batch path over the same definitions, runs you need to inspect afterwards, approvals that outlive the process that asked for them.

You do not want this if you are writing one script that calls one model. Use the Agents SDK directly, and come back when the wiring around it has become the work. You also do not want it if you have already built your own harness: AgentDeck is opinionated about project layout and configuration, and those opinions are the product.

## What it deliberately does not do

- **No DSL.** Definitions are Python. There is no YAML agent format, and there will not be one.
- **No agent loop of its own.** Bugs in the agent loop belong upstream, and improvements
  there arrive without agentdeck doing anything.
- **No sandbox.** Tools, skills and workflows are ordinary Python in your process, and a
  model-chosen tool call is trusted by design. See [SECURITY.md](SECURITY.md) before you give an
  agent something destructive.
- **No auth, no multi-tenancy, no hosted control plane, no marketplace.** `namespace` labels a
  run; it does not authenticate anyone. Put a real gateway in front of the HTTP surface.
- **No evaluation framework or prompt management.** Model prefixes select configured providers;
  AgentDeck does not add a dynamic routing service.

## Install

```bash
pip install agentdeck-sdk              # or, with the HTTP surface: agentdeck-sdk[serve]
export OPENAI_MODEL=gpt-4.1-mini OPENAI_API_KEY=sk-...
```

The distribution is **`agentdeck-sdk`**; the import stays `agentdeck`. Agents may declare
`openai/...`, `anthropic/...`, `gemini/...`, `ollama/...`, or `openrouter/...`; see
[model configuration](https://agentdecksdk.com/build-your-deck/agents). `OPENAI_BASE_URL` also
supports any OpenAI-compatible endpoint. Extras: `serve` for the
HTTP surface, `postgres` for the Postgres event log, `redis` for Redis-backed sessions or event
log, `observability` for Langfuse tracing.

Contributing to agentdeck itself is a different setup: see [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Documentation

The full documentation and live assistant demo are at **[agentdecksdk.com](https://agentdecksdk.com/)**:

* [Quickstart](https://agentdecksdk.com/meet-agentdeck/quickstart): install, configure, first agent.
* [Build Your Deck](https://agentdecksdk.com/build-your-deck/agents): agents, workflows, skills, tools, context.
* [Runs & Control](https://agentdecksdk.com/runs-and-control/runs): runs, sessions, events, lifecycle control.
* [Reference](https://agentdecksdk.com/reference/deck): every setting and every `Deck` method, generated from code.
* [Jack Documentation Agent](https://agentdecksdk.com/jack): a real reference assistant running on the docs site.
* [Runnable Examples](examples/): chat agents, human approval workflows, and skills.

If AgentDeck is useful to you, [a star](https://github.com/agentdecksdk/agentdeck) helps other developers find it.

---

## Project

* **Contributing:** [CONTRIBUTING.md](CONTRIBUTING.md). PRs target `dev`; `make check` is the gate.
* **Internals:** [`agentdeck/README.md`](agentdeck/README.md).
* **Brand:** [`docs/brand/`](docs/brand/).
* **Security:** [SECURITY.md](SECURITY.md).
* **Code of conduct:** [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
* **License:** MIT, see [LICENSE](LICENSE).

### Contributors

<a href="https://github.com/agentdecksdk/agentdeck/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=agentdecksdk/agentdeck" alt="Contributors" />
</a>
