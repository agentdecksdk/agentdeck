# Projection: mapping a protocol onto AgentDeck

The adapter's job in four mappings. AgentDeck is authoritative on every one.

Status: proposed, 2026-08-29.

## Sessions

`session_id` means conversation memory across turns. ACP Session, A2A `contextId` and a UI thread are similar and not identical. The adapter decides the mapping per protocol; none of these equalities is a core invariant.

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

`run_id` format never changes for a protocol, and values like `a2a:task:123` never enter the runtime.

## Events

Protocols consume canonical events (`agentdeck/core/events.py`): `run.started`, `text.delta`, `thought.delta`, `message.completed`, `tool.call.started`, `tool.call.completed`, `agent.changed`, `report`, `artifact.created`, `usage.reported`, `input.appended`, `run.interrupted`, `answer.refused`, `control.requested`, `control.observed`, `run.paused`, `run.resumed`, `run.completed`, `run.failed`, `run.cancelled`, `custom`.

| event | UI-style projection | A2A-style projection |
|---|---|---|
| `run.started` | generation started | Task created |
| `text.delta` | text update | message part |
| `tool.call.started` | tool part | TaskStatusUpdate |
| `report` | status or progress part | TaskStatusUpdate |
| `artifact.created` | attachment | TaskArtifactUpdate |
| `run.interrupted` | approval or question | input-required |
| `run.completed` | generation complete | Task completed |

A binding advertises what it supports (`streaming`, `text`, `hitl`, `control.cancel`, ...) and `expose()` validates that each advertised capability has its projection or action implemented. Unadvertised kinds and `UnknownEvent` are skipped with no per-event warning. Advertise it, implement it. The schema never gains protocol kinds (`a2a.task.updated`, `acp.session.updated`).

`run.interrupted` maps to the protocol's native "needs user input" mechanism and the reply maps back to `run.answer()`: A2A `INPUT_REQUIRED`, ACP `request_permission` or user interaction, MCP elicitation, AG-UI and A2UI interactive part, Native `{run_id, interrupt_id, reason, payload}` plus an `answer` route. A binding that does not advertise `hitl` refuses targets at start with `UNSUPPORTED` when an interrupt would otherwise stall its client.

## Reconnect

`Event.seq` is the common replay primitive: `run.events(from_seq=last + 1, follow=True)` resumes a lost stream without touching execution. Each binding builds its protocol's reconnect on it, with adapter-owned cursor or task state where the protocol needs more.

## Input

| protocol input | AgentDeck input |
|---|---|
| text | `str` or `TextBlock` |
| image | `ImageBlock` |
| resource | `ResourceBlock` |
| structured | `DataBlock` or workflow JSON input |

Conversion is explicit. Unrepresentable input is rejected with `INVALID_INPUT`, never dropped: the caller must never believe the model saw something AgentDeck discarded.

## Artifacts

`artifact.created` is a reference: `{artifact_id, media_type, uri, size}`. A binding projects it into its protocol-native artifact or resource type carrying that uri (A2A `FilePart` uri, MCP resource, ACP resource, AG-UI and A2UI attachment), or skips it if the protocol has none. AgentDeck does not fetch, store or proxy the bytes; the uri is owned by whoever produced it. Storage and retrieval are a separate future Artifacts epic.

## Protocol-specific state

ACP session metadata, A2A task metadata, push subscriptions, UI frontend metadata and connection IDs belong to the protocol package (its own memory, Redis or database store). Core makes no promise to persist arbitrary protocol state. A shared store contract is extracted only after several protocols need the same one.
