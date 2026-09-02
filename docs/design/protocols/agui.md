# AG-UI binding

AG-UI serves a **Deck**, not an agent: `AGUI.http()` is a projection of `DeckGateway` and `Run`
into the AG-UI protocol, and every target in the catalog is reachable through one endpoint. A
pinned single-target endpoint is convenience, not the model.

## What `AGUI.http()` promises

Two levels, held to different standards:

| level | meaning | goal |
|---|---|---|
| wire compatibility | valid `RunAgentInput` parsing, official event schemas, HTTP/SSE behavior | complete from the first merge |
| semantic capability | state, frontend tools, interrupts, multimodal, steering actually work | complete progressively |

The name is a promise: `AGUI.http()` means real AG-UI compatibility, so an unsupported stable
AG-UI feature is a tracked gap with a named AgentDeck primitive behind it, never a part of the
protocol deliberately left out. A capability is advertised only once it works.

## Public API

```python
import asyncio

from agentdeck.bindings import AGUI

asyncio.run(deck.serve(AGUI.http("/agui"), port=8000))                       # the whole catalog
asyncio.run(deck.serve(AGUI.http("/support", target="Support"), port=8000))  # pinned
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

The AG-UI binding is transport plus adapter, and the adapter is not a new architectural concept:
it is the translation half of the binding that `bindings.md` already describes. No generic adapter
base class exists until a second binding proves the same abstraction is reusable, and no other
binding is held to this file shape until one needs it.

AG-UI is the protocol and `AGUI.http()` is its first transport: protocol translation lives in
`adapter.py`, HTTP/SSE lifecycle in `binding.py`. A later persistent transport reuses the same
adapter and may define its own disconnect and reconnect semantics.

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

Two modes, held to different interoperability claims:

| mode | what the client needs to know |
|---|---|
| `AGUI.http("/support", target="Support")` | the URL, and nothing else: plain generic AG-UI |
| `AGUI.http("/agui")` | the URL plus the target name, carried in the optional `forwardedProps.agentdeck.target` extension |

`forwardedProps` is opaque application data that AG-UI itself never consumes, so a generic client
cannot discover AgentDeck targets. AG-UI defines no target-discovery vocabulary, and this binding
invents none.

Resolution is deterministic:

```text
pinned                                    → the pinned target
pinned, a different target requested      → 4xx
not pinned, a target requested            → validate it, then use it
not pinned, none requested, one target    → that target
not pinned, none requested, many targets  → 4xx naming the available targets
```

`forwardedProps.agentdeck` is reserved for AgentDeck routing. Arbitrary AG-UI context is not
converted into system prompts, `ctx.data` or runtime context: a non-empty `context` is refused with
a named reason, and a mapping is added only when it has a generic AgentDeck equivalent.

## Input and history

An AG-UI client carries the transcript; an AgentDeck session already owns the conversation. For the
initial append-only conversation capability the adapter accepts a transcript only when it represents
a normal new user turn, and sends that new user content into the existing session. The full
transcript is never replayed blindly, because replaying it would duplicate history. `parentRunId`,
transcript edits, regeneration, new system or developer messages and tool-result continuation are
explicit unsupported capabilities until AgentDeck has matching semantics.

| AG-UI content | AgentDeck |
|---|---|
| text | `TextBlock` |
| image, audio, inline | `ImageBlock`/`AudioBlock` |
| image, audio, by URL | `ResourceBlock` |
| document, video, by URL | `ResourceBlock` |
| document, video, inline bytes | refused: no block holds bytes AG-UI sent inline for a reference-only kind |
| `BinaryInputContent` | refused: no generic binary content block |
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
events, which is what keeps lifecycle synthesis in one place. `AdapterState` is what the text and
reasoning lifecycles need to know which segment is already open.

`RUN_STARTED` and `RUN_FINISHED` describe the AG-UI interaction, not the lifetime of the canonical
Run: the binding emits `RUN_STARTED` once per incoming `RunAgentInput`, and the adapter closes the
envelope from the segment's terminal event, which is the event that carries the outcome.

| AgentDeck | AG-UI |
|---|---|
| first `text.delta` for a message id | `TEXT_MESSAGE_START` then `TEXT_MESSAGE_CONTENT` |
| each later `text.delta` | `TEXT_MESSAGE_CONTENT` |
| `message.completed` | `TEXT_MESSAGE_END`, synthesizing START and CONTENT if no delta arrived |
| first `thought.delta` | `REASONING_START`, `REASONING_MESSAGE_START`, `REASONING_MESSAGE_CONTENT` |
| each later `thought.delta` | `REASONING_MESSAGE_CONTENT` |
| an open reasoning segment at a text, tool, HITL or terminal boundary | `REASONING_MESSAGE_END` then `REASONING_END`; a later `thought.delta` opens a new segment |
| `tool.call.started` | `TOOL_CALL_START`, `TOOL_CALL_ARGS`, `TOOL_CALL_END` |
| `tool.call.completed` | `TOOL_CALL_RESULT`, from the recorded result preview, never a bypass of it |
| `run.started` | nothing: the binding already opened the interaction (`rulings.md` 50) |
| `run.completed` | `RUN_FINISHED` |
| `run.failed` | `RUN_ERROR` |
| `run.interrupted` | `RUN_FINISHED` carrying the interrupt outcome |
| `run.paused` | an AgentDeck pause outcome, never a user question |
| `run.cancelled` | `RUN_ERROR(message="cancelled", code="cancelled")`, the conforming representation until AG-UI standardizes cancellation |
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

The official shapes carry the round trip, never a custom `forwardedProps` extension:

```text
AG-UI run r1  RUN_STARTED                                          →  Run A starts
              RUN_FINISHED(outcome=RunFinishedInterruptOutcome(    →  Run A interrupted at
                  interrupts=[Interrupt(id, reason, message, ...)]))  ctx.ask() / ctx.approve()
AG-UI run r2  RUN_STARTED, resume=[ResumeEntry(interruptId,        →  Run A.answer(value), the
                  status="resolved", payload=value)]                  next segment
              RUN_FINISHED(outcome=RunFinishedSuccessOutcome)      →  Run A completed
```

`run.interrupted`'s `interrupt_id` becomes `Interrupt.id`, `reason` becomes `Interrupt.reason`,
the question becomes `Interrupt.message`, and the options become `Interrupt.response_schema`,
an enum schema rather than free metadata, since AG-UI gives interrupt data a typed home. The
client answers by sending a new `RunAgentInput` whose `resume` carries one `ResumeEntry` naming
that `interruptId`; the binding reads its `payload` as the answer. A `resume` naming an
`interruptId` that is not this run's current one, or carrying more than one entry (several
outstanding interrupts is a tracked gap below), is refused with a named 4xx; `status="cancelled"`
is refused the same way until an answer-level cancel exists. The resumed AgentDeck Run is the
same Run: a new AG-UI interaction is not a new execution.

Finding the suspended Run starts with the public APIs: `threadId` is the `session_id`, and a
session has one active owner, so the `WAITING_ANSWER` run for that session is the target. If that
proves awkward or slow, it is evidence for `gateway.current_run(session_id=, namespace=)`, which
is added when the binding needs it and not before (`gateway.md`).

## Cancellation

AG-UI's own client represents cancellation by aborting the request, so `AGUI.http()` maps request
cancellation or disconnect to `Run.cancel()`. That is an AG-UI HTTP transport rule, not the default
Run-reader behavior (`rulings.md` 10 and 46): a resumable AG-UI transport may choose otherwise.

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
| `parentRunId`, transcript edit, regeneration | session or run branching, a fork semantics |
| non-empty `context` | a generic run-scoped semantic context |
| frontend tools | run-scoped tools supplied at start, useful to every protocol |
| shared state (`STATE_SNAPSHOT`, RFC-6902 `STATE_DELTA`) | a real shared application-state contract; `ctx.data` is not proven to mean the same thing |
| steering | mid-run input |
| several outstanding interrupts | `run.answer(interrupt_id, value)`; until then one outstanding ask per Run |
| tool-output streaming | richer tool progress and result events |
| inline document/video bytes | a content block for bytes held inline, not by reference; `ResourceBlock` is a uri only |
| `BinaryInputContent` | a generic binary content block; today's blocks are each typed to a kind |

Until each lands, the binding answers an explicit unsupported result rather than pretending: empty
`tools`, empty state, empty `context` and an absent `parentRunId` are supported, and anything else is
refused with a named reason. A standard field is never ignored.

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

AGUI-0 through AGUI-3 land together, in one PR (#596): the wire architecture and the capabilities
AgentDeck already has are one binding, not four staged merges. AGUI-4/5/6 wait for their own core
AgentDeck primitive and stay tracked gaps until then; AGUI-7 is #597.

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
| 21 | a pinned endpoint works with Assistant UI with zero AgentDeck-specific frontend code |
| 22 | full-Deck mode works when the client supplies `forwardedProps.agentdeck.target` |

## The DX this buys

Embedding inside an existing service uses `deck.asgi(...)` instead of `deck.serve(...)`, both endpoints on one listener:

```python
from agentdeck import Agent, Deck, WorkflowCtx, workflow
from agentdeck.bindings import AGUI

deck = Deck(agents=[support, analyst], workflows=[workflow(research), workflow(onboarding)])
app = deck.asgi(
    AGUI.http("/agui"),                                            # the catalog, target per request
    AGUI.http("/support", target="Support", name="agui-support"),   # pinned, plain AG-UI
)                                                                    # uvicorn yourmodule:app
```

```javascript
const agent = new HttpAgent({url: "/support"})
const runtime = useAgUiRuntime({agent})
```

A pinned endpoint is plain AG-UI to any client. Reaching the whole catalog costs one extension
field, `forwardedProps.agentdeck.target`, and either way AgentDeck never learns that the caller
happens to be Assistant UI.
