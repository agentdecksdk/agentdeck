# Overview

AgentDeck is a production runtime and harness for AI agents and multi-agent workflows.

Writing the prompt is the short part. Everything after it is infrastructure you did not set out to
build, and AgentDeck is that infrastructure.

## The problem it solves

| What you hit | What AgentDeck gives you |
|---|---|
| The agent forgets the last turn | sessions that carry history across runs |
| A run needs a human to approve something | pause, resume and answer on a live handle |
| The process died mid-run | durable run identity you can pick up again, in another process |
| "What did it actually do?" | an append-only log of every token, tool call and state transition |
| A tool needs your database | typed, request-scoped context injected into tools and workflows |

None of that is model work, and all of it stands between a prototype that runs and something you
can operate.

## What you keep

- **Your model provider.** An agent selects OpenAI, Anthropic, Gemini, Ollama or OpenRouter by
  model prefix, and each reads its own credential.
- **Your tools.** MCP servers attach to a `Deck` directly, and an OpenAI Agents SDK tool you
  already built passes through uncompiled. An SDK agent passes through as a handoff target.
- **Your execution.** The agent loop stays in the SDK, or in a `@workflow`, which is ordinary
  Python with no engine underneath it. AgentDeck owns configuration and orchestration, not the loop.

## What it asks of you

One composition root. Declare agents, tools, workflows and skills, hand them to a `Deck`, and run
them. AgentDeck takes over run identity, lifecycle, persistence, event dispatch, cancellation and
concurrency, and does not ask you about any of it again.

## Next

- [Quickstart](/meet-agentdeck/quickstart) - install the SDK and run your first agent.
- [What's new in 6.0](/meet-agentdeck/whats-new-6) - bindings, the new front door, and what broke.
- [Mental Model](/meet-agentdeck/mental-model) - the four primitives and how they fit together.
- [Build Your Deck](/build-your-deck/agents) - agents, tools, workflows and context in depth.
