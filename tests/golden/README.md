# Golden wire baselines

Byte-level snapshots of agentdeck's user-visible HTTP/SSE surface, recorded against
v1.2.x. They exist so the planned three-ring refactor (core events / engine adapters /
thin surfaces) can be diffed against today's behaviour instead of trusted.

A failing test here means the wire changed. That is either a bug or a deliberate,
documented change — never something to "fix" by re-recording without reading the diff.

## What is recorded

`test_golden_wire.py::capture` performs requests against the real
`agentdeck.serve.create_app()` app via `fastapi.testclient.TestClient`, in this order:

| snapshot | request |
| --- | --- |
| `01_health.http` | `GET /health` |
| `02_chat.http` | `POST /agents/Greeter/chat` |
| `03_chat_stream.http` | `POST /agents/Greeter/chat?stream=true` (every SSE frame) |
| `04_chat_missing_field.http` | `POST /agents/Greeter/chat` without `session_id` (422) |
| `05_agent_unknown.http` | `POST /agents/Nope/chat` (404) |
| `06_workflow.http` | `POST /workflows/EchoFlow` |
| `07_workflow_stream.http` | `POST /workflows/EchoFlow?stream=true` (node updates + `done`) |
| `08_interrupt_stream.http` | `POST /workflows/ApprovalFlow?stream=true&thread_id=t-golden` (`interrupt` in place of `done`) |
| `09_pending.http` | `GET /workflows/ApprovalFlow/pending` while paused |
| `10_resume.http` | `POST /workflows/ApprovalFlow/t-golden/resume` |
| `11_pending_after_resume.http` | `GET /workflows/ApprovalFlow/pending` once answered |
| `12_workflow_error.http` | `POST /workflows/BoomFlow` — node raises; 500 `{"detail": "internal error"}` |
| `13_workflow_error_stream.http` | the same `?stream=true` — in-band `error` frame |
| `14_side_effect.http` | `POST /workflows/SideEffectFlow` — a node with no update; `"delta": null` |
| `15_side_effect_stream.http` | the same `?stream=true` |
| `16_fanout_interrupt.http` | `POST /workflows/FanoutInterruptFlow` — one branch interrupts while a sibling completes; the terminal body is the interrupt (#122) |
| `17_fanout_interrupt_stream.http` | the same `?stream=true` — the completed sibling's `node_update` reaches the wire before `interrupt` replaces `done` |

Cases 12/13 exist for the one wire contract with a security property: an `AgentdeckError`
that isn't a `NotFoundError` may carry secrets (skill stderr, config values), so the
surface must render a type name and nothing else. `BoomFlow` raises a deliberately
secret-shaped message and `test_failures_never_echo_the_error_message` asserts it is
absent from both recordings.

Each snapshot file is a small HTTP-shaped record:

```
HTTP <status>
<recorded headers, one per line>

<raw response body, byte for byte>
```

The recorded headers are exactly `content-type`, `cache-control` and
`x-accel-buffering` — the three the app sets deliberately. `date`, `server` and
`content-length` are transport noise and are **not** recorded (an omission, not a
rewrite: the body itself is never touched).

## Normalization rules

**None.** No field is rewritten, masked or sorted on the way into a snapshot. Every
byte in `snapshots/` is exactly what the app wrote. That is the property that makes
this a safety net rather than a shape check, and it holds because everything variable
is pinned at the source instead:

- **The model.** `fake_model.ScriptedModel` implements the Agents SDK's `Model`
  interface directly and is injected by swapping `OpenAIProvider` wherever it is
  constructed (`scripted_model.patch_provider`, a test-only `monkeypatch.setattr`). No
  network, no API key, no real model. Response ids (`resp_golden_1`), item ids, token
  counts and `created_at` are constants in the script.
- **The script.** Turn 1 returns a `function_call` to the fixture agent's
  `lookup_slot` tool; turn 2 answers in three text deltas
  (`"Tuesday " / "at 9am " / "works."`). Both turns are identical for `get_response`
  and `stream_response`, so streamed and non-streamed captures agree. The tool call is
  visible on the wire only through `usage.requests == 2` — `serve.py` forwards text
  deltas and drops structural events, which is itself part of the recorded contract.
- **Ids the client owns.** `session_id`, `thread_id` and workflow input state are
  literals in `capture`.
- **Env and config.** `conftest._PINNED_ENV` overrides the settings knobs that would
  otherwise reach outside the test (Redis sessions, Langfuse export, the sqlite
  checkpointer) or truncate the scripted turn (`AGENTDECK_RUNNER_MAX_TURNS`). `.env` and
  `config.yaml` resolve from cwd at settings-build time (`runtime/settings.py`'s
  `resolve_env_file` / `resolve_config_path`), and `make_client` chdirs to
  `fixture_project` before building settings — that directory has no `config.yaml`, and
  any `.env` there is overridden for the keys in `_PINNED_ENV`. `APP_CONFIG_PATH` is
  still pinned to the packaged `config.default.yaml` as a belt-and-suspenders guard.
  Env vars outside `_PINNED_ENV` are still able to reach a capture; add one here rather
  than normalizing its effect away. The checkpointer is `memory`, and its process-wide
  cache is cleared per client so `/pending` never sees another capture's threads.
- **Timestamps.** Nothing on these endpoints emits one. The fixture workflows are pure
  functions of their input state.

If a future endpoint does put an irreducibly variable value on the wire, pin it in the
fake first. A normalization step is the last resort, and it must be listed in this
section — an undocumented normalization is a hole in the safety net.

## The fixture project

`fixture_project/.agentdeck/` is a minimal, committed project dir:

- `agents/greeter/agent.py` — one agent, one `function_tool`, so the scripted model can
  drive a tool-call turn.
- `workflows/echo_flow/workflow.py` — two pure nodes; the `done` path.
- `workflows/approval_flow/workflow.py` — one `interrupt()`, `durable = True`; the
  pending/resume path.
- `workflows/boom_flow/workflow.py` — one node raising a secret-shaped `SkillError`; the
  500 and SSE-`error` paths.
- `workflows/fanout_interrupt_flow/workflow.py` — a fan-out with one interrupting branch and
  one slower sibling that completes; pins the sibling's node update reaching the wire before
  the pause replaces `done` (#122).

## Running and re-recording

```bash
make test         # whole suite, golden replay included
make golden       # re-record snapshots — deliberate, never automatic
```

`make golden` is `AGENTDECK_GOLDEN_UPDATE=1 pytest tests/golden -q`. Re-record only
after deciding the new bytes are correct, and put the snapshot diff in the PR
description. Update mode also deletes snapshots that no case produces any more, so a
renamed case cannot leave an orphan behind.

Besides an intended change of ours, one other thing legitimately moves these bytes: a
dependency bump. `content-type: text/event-stream; charset=utf-8` comes from starlette
and the JSON separators from FastAPI's encoder, so a starlette / FastAPI / pydantic
upgrade can shift a snapshot with no agentdeck change at all — the diff is then the
review artifact for that bump (one is already queued: `TestClient` warns that `httpx` is
deprecated in favour of `httpx2`). Nothing recorded here depends on the Python version;
CI records on 3.13.

`test_capture_is_stable_across_runs` captures twice against two independent app
instances in one process and asserts the bytes match; CI replays the suite once more in
a separate process.
