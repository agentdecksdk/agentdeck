# AG-UI binding

AG-UI serves a **Deck**, not an agent: `AGUI.http()` is a projection of `DeckGateway` and `Run`
into the AG-UI protocol, and every target in the catalog is reachable through one endpoint. A
pinned single-target endpoint is convenience, not the model.

## What `AGUI.http()` promises

Two levels, held to different standards:

| level | meaning | goal |
|---|---|---|
| wire compatibility | valid `RunAgentInput` parsing, official event schemas, HTTP/SSE behaviour | complete from the first merge |
| semantic capability | state, frontend tools, interrupts, multimodal, steering actually work | complete progressively |

The name is a promise: `AGUI.http()` means real AG-UI compatibility, so an unsupported stable
AG-UI feature is a tracked gap with a named AgentDeck primitive behind it, never a part of the
protocol deliberately left out. A capability is advertised only once it works.

## Public API

```python
from agentdeck.bindings.agui import AGUI

deck.expose(AGUI.http("/agui"))                                  # the whole catalog
deck.expose(AGUI.http("/support", target="Support", name="agui-support"))  # pinned
```

```python
AGUI.http(
    path: str = "/agui",
    *,
    target: str | None = None,
    namespace: str | None = None,
    name: str = "agui",
) -> Binding
```

| argument | effect |
|---|---|
| `target=None` | the client names a target per interaction |
| `target="Support"` | pinned; a request naming a different target is rejected, never silently overridden |
| `namespace=` | fixed per binding, as every binding does (`rulings.md` 5) |
| `name=` | distinguishes a second instance in one exposure (`rulings.md` 41) |

## Architecture

```text
 Assistant UI / CopilotKit / any AG-UI client
                    │  AG-UI HTTP + SSE
                    ▼
               AGUI.http()          adapters/bindings/agui/
                    │
                    ▼
               DeckGateway          targets() / start() / get_run()
                    │
        ┌───────────┼───────────┐
        ▼           ▼           ▼
      Agent      Workflow    future target kind
        └───────────┼───────────┘
                    ▼
                   Run             canonical AgentDeck events
                    │
                    ▼
               AGUI adapter        both directions, one module
                    │
                    ▼
               AG-UI events
```

Nothing below `DeckGateway` knows AG-UI exists.

## Package layout

```text
agentdeck/bindings/agui.py                   the public import path
agentdeck/adapters/bindings/agui/binding.py  transport: routes, SSE, lifecycle
agentdeck/adapters/bindings/agui/adapter.py  translation, both directions
```

A binding is transport plus adapter, and the adapter is not a new architectural concept: it is
the translation half of the binding that `bindings.md` already describes. No generic adapter base
class exists until a second binding proves the same abstraction is reusable.

The official AG-UI Python models and encoder define the wire. This binding never redefines the
schema, and no generic protocol-translation framework is extracted until a second protocol needs
the same one.

## Identity

The load-bearing decision:

| AG-UI | AgentDeck |
|---|---|
| `threadId` | `session_id` |
| `runId` | the protocol interaction id, binding-owned |
| (binding config) | `namespace` |
| | `Run.id`, the canonical execution id |

`runId` is never `Run.id`, because one AgentDeck Run outlives several AG-UI interactions:

```text
AG-UI run 1 ──┐
AG-UI run 2 ──┼── one AgentDeck Run, across suspension and resume
AG-UI run 3 ──┘
```

## Target selection

`threadId` and `runId` keep their protocol meanings and are never overloaded. A target arrives as
an explicit AgentDeck extension:

```json
{"threadId": "customer-42", "runId": "interaction-18",
 "forwardedProps": {"agentdeck": {"target": "Research"}}}
```

`forwardedProps.agentdeck` is reserved for AgentDeck routing. Arbitrary AG-UI context is not
converted into system prompts, `ctx.data` or runtime context: a mapping is added only when it has
a generic AgentDeck equivalent.

## Input and history

An AG-UI client carries the transcript; an AgentDeck session already owns the conversation. The
transcript is therefore protocol context, and only the new user message becomes AgentDeck input.
Replaying the whole transcript each turn would duplicate history, so branching, editing and
regeneration are unsupported rather than guessed at.

| AG-UI content | AgentDeck |
|---|---|
| text | `TextBlock` |
| image | `ImageBlock` |
| audio | `AudioBlock` |
| document, resource | `ResourceBlock` |
| structured JSON | `DataBlock` where it is genuinely data |
| anything else | protocol error, never dropped |

No AG-UI-specific content type enters core.

## Event projection

`adapter.py` owns the translation in both directions, so the transport half holds no protocol
semantics:

```python
def to_agui_event(event: Event, state: AdapterState) -> list[BaseEvent]: ...
def to_agentdeck_input(run_input: RunAgentInput) -> Input: ...
def to_agentdeck_resume(run_input: RunAgentInput) -> object: ...
```

`to_agui_event` returns a list because one canonical event may produce zero, one or several AG-UI
events, which is what keeps lifecycle synthesis in one place. `AdapterState` is what a text
lifecycle needs to know it has already opened a message.

| AgentDeck | AG-UI |
|---|---|
| first `text.delta` for a message id | `TEXT_MESSAGE_START` then `TEXT_MESSAGE_CONTENT` |
| each later `text.delta` | `TEXT_MESSAGE_CONTENT` |
| `message.completed` | `TEXT_MESSAGE_END`, synthesising START and CONTENT if no delta arrived |
| first `thought.delta` | `REASONING_START`, `REASONING_MESSAGE_START`, `REASONING_MESSAGE_CONTENT` |
| `tool.call.started` | `TOOL_CALL_START`, `TOOL_CALL_ARGS`, `TOOL_CALL_END` |
| `tool.call.completed` | `TOOL_CALL_RESULT`, from the recorded result preview, never a bypass of it |
| `run.started` | `RUN_STARTED` |
| `run.completed` | `RUN_FINISHED` |
| `run.failed` | `RUN_ERROR` |
| `run.interrupted` | `RUN_FINISHED` carrying the interrupt outcome |
| `run.paused` | an AgentDeck pause outcome, never a user question |
| `run.cancelled` | the cancellation representation AG-UI has today |
| `run.resumed` | nothing: the next segment's own events carry it |
| `report` | `CUSTOM agentdeck.report` |
| `agent.changed` | `CUSTOM agentdeck.agent_changed`, which is what renders "Researcher is working" |
| `artifact.created` | `CUSTOM agentdeck.artifact` until AG-UI has a canonical artifact shape |
| `usage.reported` | `CUSTOM agentdeck.usage`, or omitted |
| `control.requested`, `control.observed` | ignored: execution detail, and Native exposes it losslessly |
| any kind this version has never seen | ignored |

Reasoning is projected as visible reasoning output, not as private chain of thought. A new
canonical event kind never breaks the projection.

Whether the target is an agent or a workflow makes no difference: both produce a Run, and a
workflow that streams text, calls tools, delegates to agents, reports progress, asks the user,
pauses and completes projects through exactly the same table. That equivalence is the point.

## HITL and resume

```text
ctx.ask(...) / ctx.approve(...)  →  run.interrupted  →  RUN_FINISHED(interrupt outcome)
AG-UI resume interaction         →  run.answer(value) on the same Run  →  RUN_STARTED, next segment
```

The question and its options become the interrupt's metadata. The resumed AgentDeck Run is the
same Run: a new AG-UI interaction is not a new execution.

Finding the suspended Run starts with the public APIs: `threadId` is the `session_id`, and a
session has one active owner, so the `WAITING_ANSWER` run for that session is the target. If that
proves awkward or slow, it is evidence for `gateway.current_run(session_id=, namespace=)`, which
is added when the binding needs it and not before (`gateway.md`).

## Cancellation

```text
v1 transport rule: an AG-UI HTTP disconnect or abort means Run.cancel(...)
```

Stated as a transport-level rule because it stops being true the day AG-UI gains a resumable
transport, at which point a disconnect can no longer imply cancellation.

## Errors

| when | failures | result |
|---|---|---|
| before the stream opens | malformed JSON, invalid `RunAgentInput`, missing or unknown target, unsupported capability | HTTP 4xx |
| after the run starts | `InputError`, `SessionBusyError`, `RunStateError`, `run.failed`, anything internal | `RUN_ERROR` |

The Native invariant holds: a caller-safe failure carries a useful message, an internal one
carries "internal error", and raw exception text never reaches the wire.

## Authentication and tenancy

`namespace` is fixed per binding (`AGUI.http("/agui", namespace="acme")`), a resolver comes later
if it is needed, and authentication belongs at the binding or host boundary. `threadId` is session
identity and never authorization. The Deck stays unaware of both.

## Native and AG-UI are different jobs

```text
                        Native      lossless: every canonical event, Run identity, seq replay,
                          ▲         control, pending, answer, operational detail
                          │
      AgentDeck Run ──────┼──────── AG-UI       the human and application facing projection
                          │
                          ├──────── A2A         the agent-to-agent projection
                          └──────── future protocols
```

AG-UI represents the interaction. Native remains the full lifecycle protocol.

## Gaps, and the AgentDeck primitives behind them

Each row is a stable AG-UI feature that needs a generic AgentDeck capability. None of them is
faked inside the binding, because a protocol-shaped workaround in a binding is exactly what
`invariants.md` forbids.

| AG-UI feature | missing AgentDeck primitive |
|---|---|
| frontend tools | run-scoped tools supplied at start, useful to every protocol |
| shared state (`STATE_SNAPSHOT`, RFC-6902 `STATE_DELTA`) | a real shared application-state contract; `ctx.data` is not proven to mean the same thing |
| steering | mid-run input |
| several outstanding interrupts | `run.answer(interrupt_id, value)`; until then one outstanding ask per Run |
| tool-output streaming | richer tool progress and result events |

Until each lands, the binding answers an explicit unsupported result rather than pretending: empty
`tools` and empty state are supported, non-empty are refused with a named reason.

## Roadmap

| slice | contents |
|---|---|
| AGUI-0 | conformance foundation: official models, full `RunAgentInput` parsing, HTTP/SSE, a validation suite |
| AGUI-1 | what AgentDeck already has: full Deck target routing, text, reasoning, backend tools, sessions, cancel, custom events |
| AGUI-2 | HITL: interrupts, approval, resume, addressing several interrupts |
| AGUI-3 | multimodal input |
| AGUI-4 | frontend tools, on a new generic run-scoped tool capability |
| AGUI-5 | shared state, on a new generic shared-state capability, then `STATE_SNAPSHOT` and `STATE_DELTA` |
| AGUI-6 | steering, and richer long-running tool output |
| AGUI-7 | official AG-UI conformance tests, plus Assistant UI, CopilotKit and the official `HttpAgent` |

AGUI-0 merges first and alone: the wire architecture has to be right before capabilities fill in,
and a capability added on a shaky wire foundation is a rewrite.

## Tests

| # | test |
|---|---|
| 1 | a Deck with both an agent and a workflow target serves both |
| 2 | `forwardedProps.agentdeck.target` routes to the named target |
| 3 | a pinned binding rejects a request naming a different target |
| 4 | two AG-UI bindings coexist in one exposure using `name=` |
| 5 | `threadId` becomes `session_id` |
| 6 | a second user turn reuses the same AgentDeck session |
| 7 | text deltas produce a valid AG-UI message lifecycle |
| 8 | a workflow's text projects identically to an agent's |
| 9 | `thought.delta` projects to the reasoning family |
| 10 | backend tool calls and results project correctly |
| 11 | `ctx.ask()` produces an AG-UI interrupt carrying question and options |
| 12 | resume continues the same AgentDeck Run |
| 13 | a new AG-UI `runId` does not replace `Run.id` |
| 14 | an invalid answer is a protocol-safe error and the Run stays waiting |
| 15 | a namespace isolates runs between two bindings |
| 16 | a client disconnect cancels the Run |
| 17 | internal exception text never reaches the wire |
| 18 | an unknown canonical event kind does not break the projection |
| 19 | every emitted event validates against the official AG-UI models |
| 20 | the official AG-UI client consumes the endpoint |
| 21 | Assistant UI consumes the endpoint with no AgentDeck-specific frontend code |

## The DX this buys

```python
from agentdeck import Agent, Deck, WorkflowCtx, workflow
from agentdeck.bindings.agui import AGUI

deck = Deck(agents=[support, analyst], workflows=[workflow(research), workflow(onboarding)])
app = deck.expose(AGUI.http("/agui")).asgi()
```

```javascript
const agent = new HttpAgent({url: "/agui"})
const runtime = useAgUiRuntime({agent})
```

The frontend picks any target in the catalog, and AgentDeck never learns that the caller happens
to be Assistant UI.
