<div align="center">

<img src="docs/brand/logo-blue.svg" alt="AgentDeck" width="76">

# AgentDeck SDK

**Agentic software should feel like software.**

Build agents, tools and workflows as normal software.
AgentDeck gives them one execution model you can observe, control and extend.

<!-- The blue variant, not logo.svg: that one is fill="currentColor" for inlining, and an SVG
     loaded through <img> has nothing to inherit from, so it renders black  -  invisible on
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

The rest of this page builds one real application, a step at a time. Its name is Jack, he
answers questions about AgentDeck, and he is the assistant running on
[agentdecksdk.com](https://agentdecksdk.com/) right now. Everything below is his actual source.

## Let's build Jack

An agent is a declaration. A Deck is where an agentic application comes together.

```python
from agentdeck import Agent, Deck

jack = Agent(
    name="Jack",
    instructions="Help developers build with AgentDeck.",
)

deck = Deck(agents=[jack])
```

## Jack needs to know things

Tools do work; agents make decisions. A tool that takes a `ToolCtx` stays an ordinary function,
because the model is offered only the arguments it can actually choose: it never sees `docs`.

```python
from agentdeck import Agent, ToolCtx, Deck

def search_docs(query: str, docs: ToolCtx[DocsCorpus]) -> str:
    """Find AgentDeck documentation pages matching a query."""
    return docs.data.search(query)

def read_doc(slug: str, docs: ToolCtx[DocsCorpus]) -> str:
    """Read one AgentDeck documentation page in full, by its slug."""
    return docs.data.pages[slug]

def read_changelog(subject: str, docs: ToolCtx[DocsCorpus]) -> str:
    """Read AgentDeck's release history, by version or by topic."""
    return docs.data.changelog(subject)

jack = Agent(
    name="Jack",
    instructions="Help developers build with AgentDeck.",
    tools=[search_docs, read_doc, read_changelog],
)

deck = Deck(agents=[jack], context=DocsCorpus)
```

`context=DocsCorpus` is the *type*; the instance goes in per run. Declaring it makes `build()`
check every `ToolCtx[...]` in the catalog before a question is ever asked, so the wrong type
raises at startup rather than mid-answer.

## Everything becomes one execution tree

Run him, and the run is a first-class thing with an ordered event log:

```python
async with deck:
    async for event in deck.stream("Jack", "how do I pause a run?", context=corpus):
        print(event.kind)  # run.started, tool.call.started, text.delta, run.completed
```

Every managed invocation appends to that one log, whatever started it. Status is folded from
the log rather than stored beside it, so there is no second source to disagree with.

## Execution you can steer

A Run is the root execution, and control belongs to the handle rather than to a separate
lifecycle API:

```python
run = await deck.runs.start("Jack", question, context=corpus)

await run.pause()
await run.resume()
await run.cancel()

await run.answer({"approved": True})   # finish a run parked at an interrupt
```

`deck.runs.get(id)` rehydrates that handle in another process, so a run paused by a web request
can be resumed by a worker. Two handles on one run always agree: the durable store is the only
thing either reads.

## Jack is real, and you can use him now

The panel on **[agentdecksdk.com](https://agentdecksdk.com/)** is that agent. Ask it something
about AgentDeck and it searches these docs, reads the pages it finds, and cites them.

He is three tools over one `ToolCtx[DocsCorpus]`, streaming the run's own canonical events to
the browser over SSE with no translation layer on either side, and he is the thing serving the
site: an origin check, a per-day quota, a token ceiling, and an allowlist deciding which event
kinds a browser may see are all parts a public endpoint needs and a demo skips.

**[How Jack is built](https://agentdecksdk.com/jack)** walks the whole application, and
**[Implementation notes](https://agentdecksdk.com/jack/notes)** records each decision and the
alternative it beat. The source is [`examples/jack`](examples/jack/).

He is also the honest test of the pitch. If *"agents you have to operate"* meant anything, it
had to survive being operated.

## Where your definitions live

Everything you define lives in a `.agentdeck/` directory next to where you run. The path *is*
the registration: no catalog file, no `__init__.py`, no decorator to remember.

```text
.agentdeck/
├── agents/greeter/agent.py            # an Agent(...)
├── workflows/new_booking/workflow.py  # a @workflow function
└── skills/parse-request/              # SKILL.md + optional scripts
```

```python
async with Deck.from_project() as deck:   # discovers ./.agentdeck, fails fast
    result = await deck.run("Greeter", "hello")
```

`Deck` discovers, compiles and validates all of it before the first turn: a missing skill, an
unknown MCP name or a workflow that cannot compile fails at `build()`, not in production.

Runnable projects are in [`examples/`](examples/): a chat agent with a tool, a workflow that
pauses for human approval, an agent with skills, and
[Jack](https://agentdecksdk.com/jack). All are built by the test suite, so none can quietly stop
working.

## You build the behavior. AgentDeck manages the machinery.

Your code stays about the behavior and the structure of your application. Everything a run
needs around it already has a place, and they were designed to work together: less machinery to
build today, and nothing to retrofit when you need the next one.

| You own | AgentDeck owns |
| --- | --- |
| agents, tools, workflows | **Events.** One ordered stream of what happened. |
| what progress means | **Reporting.** Progress and status, sent from inside the work. |
| when work should stop | **Control.** Execution paused, resumed or cancelled at safe points. |
| when a person decides | **Interaction.** Branches that wait for external input. |
| business state | **State.** Sessions that outlive a single call. |
| your UI and integrations | **Surfaces.** Observers, HTTP and your UI read the same run. |

The complexity is still there. It just lives in the layer built for it.

**AgentDeck executes native @workflow and @tool targets alongside the
[OpenAI Agents SDK](https://github.com/openai/openai-agents-python) for agents.** An `Agent` compiles
to an SDK agent, and a `@workflow` coordinates executions with native async Python control. You keep
native access when you need it.

## Who it is for

You want this if you are putting agents somewhere they have to keep working: several agents and
workflows in one project, a chat surface and a batch path over the same definitions, runs you
need to inspect afterwards, approvals that outlive the process that asked for them.

You do not want this if you are writing one script that calls one model. Use the Agents SDK
directly, and come back when the wiring around it has become the work. You also do not want it
if you have already built your own harness: AgentDeck is opinionated about project layout and
configuration, and those opinions are the product.

## What it deliberately does not do

- **No DSL.** Definitions are Python. There is no YAML agent format, and there will not be one.
- **No execution engine of its own.** Bugs in the agent loop belong upstream, and improvements
  there arrive without agentdeck doing anything.
- **No sandbox.** Tools, skills and workflow nodes are ordinary Python in your process, and a
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
HTTP surface, `durability` for the Postgres checkpointer and event store (SQLite ships in base),
`redis` for Redis-backed sessions or event log, `observability` for Langfuse tracing.

Contributing to agentdeck itself is a different setup: see [CONTRIBUTING.md](CONTRIBUTING.md).

## Documentation

The full docs are at **[agentdecksdk.com](https://agentdecksdk.com/)**:

- [Quickstart](https://agentdecksdk.com/meet-agentdeck/quickstart)  -  install, configure,
  first agent
- [Build Your Deck](https://agentdecksdk.com/build-your-deck/agents)  -  agents, workflows, skills,
  tools, context
- [Runs & Control](https://agentdecksdk.com/runs-and-control/runs)  -  runs, sessions, events,
  lifecycle control
- [Reference](https://agentdecksdk.com/reference/deck)  -  every setting and every `Deck`
  method, generated from the code

If AgentDeck is useful to you, [a star](https://github.com/agentdecksdk/agentdeck) helps other
developers find it.

## Project

AgentDeck is beta software under active development; breaking changes are listed in
[CHANGELOG.md](CHANGELOG.md).

- **Contributing**  -  [CONTRIBUTING.md](CONTRIBUTING.md). PRs target `dev`; `make check` is the
  gate. Framework internals are laid out in [`agentdeck/README.md`](agentdeck/README.md).
  Issues labelled [`good first issue`](https://github.com/agentdecksdk/agentdeck/labels/good%20first%20issue)
  are scoped to be finishable in an afternoon and each one names the example to run first.
- **Brand**  -  [`docs/brand/`](docs/brand/).
- **Security**  -  [SECURITY.md](SECURITY.md), including what is deliberately out of scope.
- **Code of conduct**  -  [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
- **License**  -  MIT, see [LICENSE](LICENSE).

### Contributors

<a href="https://github.com/agentdecksdk/agentdeck/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=agentdecksdk/agentdeck" alt="Contributors" />
</a>
