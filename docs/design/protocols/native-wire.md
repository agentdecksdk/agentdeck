# Native wire spec

`native-wire v1`. Independent of `PROTOCOL_SPI_VERSION`: this is the HTTP contract `Native.http()` serves, not the SPI a plugin builds against.

Status: proposed, 2026-08-29. Every route reaches a Deck through `DeckGateway` or a public `Run` method (`docs/design/protocols/gateway.md`); a test (`tests/bindings/test_native_binding.py`) diffs the table below against the app's own routes, so they cannot drift.

## Routes

| method | path | request body | response body | error codes |
|---|---|---|---|---|
| GET | `/targets` | none | `[{name, kind, description, input_schema}]` | none |
| POST | `/runs` | `{target, input, session_id?, key?}` | run summary | 422, 409, 500 |
| GET | `/runs` | query: `status?`, `limit?` | `[run summary]` | 422, 500 |
| GET | `/runs/{run_id}` | none | run summary | 404, 500 |
| GET | `/runs/{run_id}/events` | `Last-Event-ID` header or `?from_seq=` | SSE, see below | 404, 500 |
| POST | `/runs/{run_id}/cancel` | `{reason?}` | `{}` | 404, 409, 501, 500 |
| POST | `/runs/{run_id}/pause` | `{reason?}` | `{}` | 404, 409, 501, 500 |
| POST | `/runs/{run_id}/resume` | none | `{}` | 404, 409, 501, 500 |
| GET | `/runs/{run_id}/pending` | none | an `InterruptResult` or `null` | 404, 500 |
| POST | `/runs/{run_id}/answer` | `{value}` | `{}` | 404, 409, 422, 500 |

A run summary is `{run_id, namespace, session_id, status, can: {pause, resume, cancel}}`.

`input` on `POST /runs` is forwarded to `DeckGateway.start` unchanged: a string for free text, or whatever a workflow target's own parameters accept. Rejected non-text content for an agent target surfaces as 422, named by the coercion failure.

## Errors

Every non-2xx body is `{"detail": "<message>"}`. `GatewayFailureCode` maps onto HTTP status:

| code | status |
|---|---|
| `NOT_FOUND` | 404 |
| `BUSY` | 409 |
| `CONFLICT` | 409 |
| `INVALID_INPUT` | 422 |
| `UNSUPPORTED` | 501 |
| `INTERNAL` | 500, message fixed to `"internal error"`, never the exception's own text |

`RunStateError` and `UnsupportedControlError` from a `Run` control method (not gateway-wrapped: `cancel`/`pause`/`resume`/`answer` live on `Run`, not `DeckGateway`) map to `CONFLICT`/`UNSUPPORTED` the same way, with their own message.

## SSE framing and reconnect

One frame per event, nothing reshaped:

```text
id: <seq>
data: <Event.model_dump_json()>

```

`GET /runs/{run_id}/events` pulls the first event before the response commits, so a refusal (`NOT_FOUND`) is a status code, never a stream that opens and stops. A read follows one segment (`docs/design/protocols/rulings.md` 29): it ends at a terminal event or a suspension, and the client re-tails after answering or resuming.

Reconnect is `Event.seq`-based (ruling 28): a client sends `Last-Event-ID` with the last id it saw and resumes from `id + 1`; `?from_seq=` is a caller-computed starting point instead, taken as given (`from_seq=0` replays the whole run).
