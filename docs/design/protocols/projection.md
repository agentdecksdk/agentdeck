# Projection: mapping a protocol onto AgentDeck

The adapter's mappings. AgentDeck is authoritative on every one.

## Sessions

`session_id` is conversation memory across turns. ACP Session, A2A `contextId` and a UI thread are similar but not identical, so the adapter maps them per protocol; no such equality is a core invariant.

| protocol concept | likely mapping |
|---|---|
| UI thread | `threadId → session_id` |
| A2A `contextId` | `contextId → session_id`, task metadata held by the adapter |
| ACP session | adapter-local state plus `session_id` |

## Run identity

`(namespace, run_id)` is the address. Three kinds of external id, three rules:

| external id | maps to |
|---|---|
| conversation identity (`contextId`, `threadId`, ACP session) | `session_id`, when it is semantically a conversation |
| request or task identity (A2A `taskId`, JSON-RPC id) | adapter-local mapping to the Run address |
| retry or idempotency id | `key`, only when the protocol defines retry semantics |

No protocol-shaped id (`a2a:task:123`) ever enters the runtime.

## Events

`agentdeck/core/events.py` holds the canonical vocabulary. Only the events whose projection needs a ruling are listed here.

| event | UI-style projection | A2A-style projection |
|---|---|---|
| `run.started` | generation started | Task created |
| `text.delta` | text update | message part |
| `tool.call.started` | tool part | TaskStatusUpdate |
| `report` | status or progress part | TaskStatusUpdate |
| `artifact.created` | attachment | TaskArtifactUpdate |
| `run.interrupted` | approval or question | input-required |
| `run.completed` | generation complete | Task completed |

A binding advertises what it supports (`streaming`, `text`, `hitl`, `control.cancel`, ...) and `expose()` validates that each advertised capability has a projection or action. Unadvertised kinds and `UnknownEvent` are skipped silently. The schema never gains protocol kinds (`a2a.task.updated`).

`run.interrupted` maps to the protocol's native needs-input mechanism, and the reply maps back to `run.answer()`: A2A `INPUT_REQUIRED`, ACP `request_permission`, MCP elicitation, AG-UI and A2UI interactive part, Native `{run_id, interrupt_id, reason, payload}` plus an `answer` route. A binding that does not advertise `hitl` refuses at start with `UNSUPPORTED`.

## Reconnect

`Event.seq` is the replay primitive: `run.events(from_seq=last + 1, follow=True)` resumes a lost stream without touching execution. Each binding builds its protocol's reconnect on it, adding cursor or task state where the protocol needs more.

## Input

| protocol input | AgentDeck input |
|---|---|
| text | `str` or `TextBlock` |
| image | `ImageBlock` |
| resource | `ResourceBlock` |
| structured | `DataBlock` or workflow JSON input |

Conversion is explicit. Unrepresentable input is rejected with `INVALID_INPUT`, never dropped: the caller must never believe the model saw something AgentDeck discarded.

## Artifacts

`artifact.created` is a reference: `{artifact_id, media_type, uri, size}`. A binding projects it into its protocol's artifact or resource type carrying that uri (A2A `FilePart`, MCP resource, AG-UI attachment), or skips it. AgentDeck never fetches, stores or proxies the bytes; storage is a separate Artifacts epic.

## Protocol-specific state

ACP session metadata, A2A task metadata, push subscriptions and connection IDs live in the protocol package's own store; core persists no protocol state. A shared store contract waits until several protocols need the same one.
