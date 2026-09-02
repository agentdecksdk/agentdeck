# Roadmap

## Sequence

| phase | deliverable | done when |
|---|---|---|
| 1 | contracts only: `DeckGateway` over `deck.runs`, `TargetInfo`, `Capabilities`, `GatewayFailureCode`, `Binding` with `start`/`stop`, endpoint types, `Exposure`, channel-shaped fixture plugin | every contract test below that needs no real protocol passes against a fake binding |
| 2 | Native HTTP and `Terminal.stdio()` (the first surface) as bindings, with a versioned wire spec and `@agentdeck/client` through the gateway; `agentdeck/serve.py`, all of `surfaces/`, the `agentdeck-serve` script and the goldens deleted; `agentdeck chat` runs `Terminal.stdio()` | imports nothing private; no v1 route survives; `engineering/architecture.md` ownership table updated |
| 3 | `AGUI.http()` (protocol) | CopilotKit's agent-user event stream, target routing, HITL ask/resume, multimodal input, client-disconnect cancel (`agui.md`) |
| 4 | (amended: #554) `A2A.http()` (protocol) and `WhatsApp.http()` (channel), deferred to v6.x | webhook ACK then Exposure-owned tail, `message.completed` posting, reply buttons for HITL, durable phone-to-run map, AgentCard from `TargetInfo` |
| 5 | freeze SPI v1 (amended: #554) | Native, Terminal and AG-UI run on one Deck: a run started over AG-UI is visible and answerable from Native, AG-UI receives the resumed segment, every contract test passes (`tests/bindings/test_three_bindings_one_deck.py`) |
| 6 | A2A, WhatsApp, A2UI, ACP, MCP server as 6.x reference and convenience bindings; `.agentdeck/bindings` config, CLI flags, extras, docs | v6.x |

Phase 1 is small because `deck.runs` already has the gateway's shape (`gateway.md`).

## Target protocols

| protocol | binding | transport | notes |
|---|---|---|---|
| Native | `Native.http()` | HTTP/SSE | phase 2, the reference; the AgentDeck protocol, versioned spec plus JS client (`rulings.md` 18) |
| AG-UI | `AGUI.http()` | HTTP/SSE | v6.0.0; CopilotKit's agent-user event stream (`agui.md`) |
| A2UI | `A2UI.http()` | HTTP/SSE | v6.x; Google's declarative agent-to-UI protocol |
| ACP | `ACP.stdio()`, later `ACP.http()` | stdio JSON-RPC | v6.x |
| A2A | `A2A.http()`, later `A2A.grpc()` | HTTP JSON-RPC | v6.x, the reference protocol (amended: #554, deferred from phase 3) |
| WhatsApp | `WhatsApp.http()` | HTTP webhook + Cloud API | v6.x, the reference channel (amended: #554, deferred from phase 4) |
| MCP server | `MCP.stdio()`, `MCP.http()` | stdio or streamable HTTP | one tool per target, progress notifications, elicitation for HITL, runs as resources (`rulings.md` 17) |
| Terminal | `Terminal.stdio()` | stdio | surface; `agentdeck chat`; phase 2 (`rulings.md` 35) |

Every decision behind this sequence is in [`rulings.md`](rulings.md); the ones that shape it most
are 34 (`surfaces/` deleted), 36 (package names) and 37 (the v6.0 set, amended: #554). v6.0.0 is
Native, Terminal and AG-UI on one Deck:

```python
deck.serve(Native.http(), AGUI.http(), Terminal.stdio())
```

## Delivery

One `gh stack` rooted on `dev`; PR 1 is this design (#539, closes #543). Epic #129 carries one issue per story (#544 to #554) and is the plan of record. Each story opens its stacked draft PR when it starts, with the design in the PR body, and closes its issue. The Artifacts epic is independent and not on this stack.

## Contract tests before SPI v1

One test (or pair) per guarantee, named so the freeze evidence (`spi.md` "Frozen at v1") can point
at each row directly.

| guarantee | test |
|---|---|
| A protocol starts a Run without touching Runtime | `test_contract.py::test_a_message_starts_an_ordinary_run_and_its_tail_posts_the_reply` |
| A protocol tails canonical events | `test_native_binding.py::test_events_streams_raw_event_json_with_seq_as_id` |
| A disconnected reader does not cancel execution | `test_contract.py::test_a_disconnected_reader_does_not_cancel_the_run` |
| A protocol reconnects from `Event.seq` | `test_native_binding.py::test_events_reconnect_with_last_event_id_resumes_after_that_seq` |
| A protocol recovers a Run by identity | `test_contract.py::test_durable_map_survives_a_simulated_restart` |
| Cancel maps to `Run.cancel()`; pause/resume map to the same Run | `test_bindings_gateway.py::test_start_get_list_delegate_to_deck_runs_and_return_real_runs` (the gateway hands back real `Run`s) plus `test_terminal_binding.py::test_cancelling_mid_run_records_the_cancel_then_re_raises` |
| An interrupt is answered through `Run.answer()` | `test_native_binding.py::test_pending_and_answer_over_the_wire_then_re_tail` |
| Two protocols expose the same Deck concurrently and see the same Run | `test_contract.py::test_two_bindings_share_one_deck_and_see_the_same_run` |
| A Run started through protocol A is an ordinary AgentDeck Run | `test_three_bindings_one_deck.py` (AG-UI starts, Native reads it back) |
| Protocol metadata never appears in canonical Event payloads | `test_three_bindings_one_deck.py` (Native's raw event text carries none of AG-UI's own wire vocabulary) |
| Unknown future Event kinds do not crash a plugin | `test_contract.py::test_an_unknown_event_kind_is_skipped_not_raised` |
| Unsupported content is rejected, not dropped | `test_contract.py::test_the_webhook_maps_every_rejection_to_an_http_code` |
| A plugin imports no private AgentDeck module (import-linter contract) | `make lint-imports` against `tests/bindings/fixture_plugin/.importlinter` |
| HTTP bindings share one listener | `test_bindings_exposure.py::test_two_http_bindings_share_one_listener_each_sees_only_its_own_routes` |
| stdio works with no HTTP installed | `test_bindings_exposure.py::test_serve_stdio_only_never_imports_uvicorn` |
| Partial startup rolls back | `test_bindings_exposure.py::test_failed_start_on_binding_three_rolls_back_and_closes_owned_deck` |
| Deck and binding ownership shut down in the right order | `test_bindings_exposure.py::test_the_binding_whose_start_raised_is_stopped_before_the_earlier_ones` |
