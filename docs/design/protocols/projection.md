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

`(namespace, run_id)` is the address. A protocol may expose it directly (native HTTP, UI generation id) or under its own name (A2A `taskId`). The mapping is the adapter's. `run_id` format never changes for a protocol, and values like `a2a:task:123` never enter the runtime.

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

A binding declares the closed set of kinds it projects; unmapped kinds and `UnknownEvent` are skipped with no per-event warning. At build time the binding proves it covers the required categories (terminal state, text output, `run.interrupted`) or construction fails. The schema never gains protocol kinds (`a2a.task.updated`, `acp.session.updated`).

`run.interrupted` maps to the protocol's native "needs user input" mechanism and the reply maps back to `run.answer()`: A2A `INPUT_REQUIRED`, ACP `request_permission` or user interaction, MCP elicitation, AG-UI and A2UI interactive part, Native `{run_id, interrupt_id, reason, payload}` plus an `answer` route. Every target can interrupt, so every binding must map it.

## Reconnect

`Event.seq` is the reconnect cursor. `run.events(from_seq=last + 1, follow=True)` resumes a lost stream without touching execution. This is why the event log stays below every protocol.

## Input

| protocol input | AgentDeck input |
|---|---|
| text | `str` or `TextBlock` |
| image | `ImageBlock` |
| resource | `ResourceBlock` |
| structured | `DataBlock` or workflow JSON input |

Conversion is explicit. Unrepresentable input is rejected with `INVALID_INPUT`, never dropped: the caller must never believe the model saw something AgentDeck discarded.

## Artifacts

`artifact.created` is projected as metadata or a reference only, never bytes. Bytes stream through `gateway.open_artifact()`; each binding serves them its own way (A2A `FilePart` uri, MCP resource, ACP resource, AG-UI and A2UI attachment URL, Native download route) and never writes that URL into the event.

## Protocol-specific state

ACP session metadata, A2A task metadata, push subscriptions, UI frontend metadata and connection IDs belong to the protocol package (its own memory, Redis or database store). Core makes no promise to persist arbitrary protocol state. A shared store contract is extracted only after several protocols need the same one.
