# agentdeck v3.0.0 — outsider review, united report

**Method.** Independent reviewers in clean-room workspaces containing only what a real early
adopter gets: the built `agentdeck-3.0.0` wheel, `README.md`, the public docs-site content, and the
shipped `examples/`. No repo access, no `docs/`, no CLAUDE.md, no issues, no web. Two rounds:

| | model | territory | outcome |
|---|---|---|---|
| A | haiku | agent surface | live half **blocked**, no API key — discovery/validation/HTTP startup only |
| B | haiku | workflow surface | ran end to end, no defects found |
| C | sonnet | agent surface + adversarial | key never arrived; **stubbed the model endpoint** and reproduced 6 findings anyway |
| D | sonnet | ops surface + adversarial | ran end to end, **7 defects, 1 of them P0** |

Round 2 added what round 1 lacked: a required adversarial phase, expectations recorded in `plan.md`
*before* coding, evidence tags (`[REPRODUCED]`/`[STATIC]`/`[BLOCKED]`), a doc-open trail, and a rule
that "the docs don't cover X" needs a pasted grep proving absence. Round 1 found zero defects; round
2's ops half alone found seven. **The model mattered less than the adversarial phase** — B and D built
the same kind of workflow, and only the one told to break it found anything.

Every claim below was re-verified against the tree before it was written down. Claims that did not
survive verification are listed in "Not confirmed".

---

## P0 — `cancel()` on an approval waiting for a human is a silent, unaudited no-op

An operator sees a high-severity run parked for sign-off, decides against it, calls
`deck.cancel(run_id)`, gets `True`. The run is not dead. Anyone — a stale queue worker, a retried
webhook, a second operator — can still call `answer(run_id, "yes")` and **the remediation executes
for real**, with nothing in the event log recording that a cancel was ever asked for.

D reproduced it end to end; the trace is confirmed in source:

- `deck.cancel()` → `Runtime.signal()` writes to the `ControlPort` only, never the event log.
- The only path that honors a pending `CANCEL` is `resume_run()` (`service.py:259-265`), which
  locates its target through `_paused()` — and `_paused()` lists **`RunStatus.PAUSED` only**
  (`service.py:370`). A run parked at an interrupt is `WAITING_HUMAN`. `deck.resume()` cannot even
  see it.
- `deck.answer()` → `Runtime.resume()` (`service.py:177`), which **never polls the control port** at
  all (verified by grep of the method body).
- `ControlRequested` is emitted in exactly two places: that `resume_run` cancel branch, and `Gate`
  at an engine safe point (`core/control.py:74`) — which the LangGraph engine never reaches. So a
  cancel against a parked approval leaves **zero trace**.

`signal()`'s own docstring is precise about the case it does handle — "cancelling a run that is
already **paused** … is honored by the next `resume_run`". There is simply no equivalent for
`WAITING_HUMAN`, and `cancel()` returns `True` either way.

The docs say pause/cancel "does not reach a workflow run today." They do not say the signal
disappears without a record while `cancel()` still reports success. That gap between "doesn't reach
it" and "vanishes silently while returning True" is the defect.

Smallest fix: poll the control port in `resume()`/`answer()` the way `resume_run()` already does, and
honor a pending `CANCEL` for `WAITING_HUMAN`. Failing that, `cancel()` must stop returning `True`
for a target that structurally cannot observe it.

**This is a shipped-stable defect on the human-approval path — the flagship feature. It reads like a
v3.0.1, not a v3.1.**

## P0 — shipped defaults strand approvals (cross-confirmed, B and D)

`AGENTDECK_CHECKPOINT` defaults to durable sqlite. `AGENTDECK_EVENTS` defaults to `memory://`.
`pending()` projects the waiting-run inbox **from the event store** (`service.py:500`), and
`answer()` needs a `run_id` only `pending()` hands out. So the default pairing is the one
combination where a `durable=True` run parks somewhere nothing can find it. Reproduced on the
shipped example, defaults untouched:

```
PARKED: {'type': 'interrupt', 'payload': {'question': 'Refund EUR 51.0 …'}, 'thread_id': 'refund-A-1003'}
--- new process, same checkpoint, default events ---
PENDING AFTER RESTART: []
```

D established it is worse and better than it looks: the run is **not** actually lost — calling the
workflow's own `resume(thread_id, …)` directly still finishes it. It is lost *from the documented
inbox*, which is the only route an operator has.

It is documented (`examples/workflow-with-an-approval/README.md:42`, and
`concepts/choosing-a-store-backend.mdx`) and the startup warning fires every time. Both reviewers
read those and both still called it a footgun. The prose is done; **the default is what's wrong.**

## P1 — the reverse pairing lists approvals that can never be answered

D's store-pairing matrix, all reproduced across real process restarts:

| `AGENTDECK_CHECKPOINT` | `AGENTDECK_EVENTS` | parked approval after restart |
|---|---|---|
| durable sqlite *(default)* | `memory://` *(default)* | **invisible** to `pending()`; still resumable if you already know the `thread_id` |
| `memory://` | durable sqlite | **visible** in `pending()`, and `answer()` always raises `ValidationError` — the state was never checkpointed, so the graph re-hydrates empty |
| durable sqlite | durable sqlite | visible and answerable — the only pairing that delivers |
| `memory://` | `memory://` | nothing survives (not re-run; trivially true) |

```
pydantic_core._pydantic_core.ValidationError: 1 validation error for IncidentState
alert  Field required [type=missing, input_value={}, input_type=dict]
```

Two of the four documented pairings produce an inbox that lies, in opposite directions. Neither
fails at startup, when both URLs are already known.

## P1 — a default install cannot satisfy its own default checkpoint

`AGENTDECK_CHECKPOINT` defaults to `sqlite://…`, but the sqlite saver ships in `[durability]`. So
`pip install agentdeck` + `durable=True` = ImportError on first run. Both B and D hit it; the error
text names the exact command and both praised it. The defect is that a **default setting requires a
non-default install**. Ship the saver, change the default, or preflight at `build()`.

## P2 — the docs' own interrupt idiom does not type-check

`concepts/workflows.mdx:100` teaches:

```python
if paused["type"] == "interrupt":
```

```
error: "__getitem__" method not defined on type "TurnResult" (reportIndexIssue)
```

`Deck.run` is annotated `-> TurnResult | Any` (`deck.py:705`) — one method returning a `TurnResult`
for an agent and a state dict or `InterruptResult` for a workflow. Prose-honest, type-useless: a
checker narrows to the `TurnResult` member and rejects every workflow-path access. A caller running
a checker in CI must `cast()` the SDK's own documented example.

Round 1 hit this by accident (haiku's code passed at runtime, failed the checker on the same lines);
round 2 hit it deliberately, on the doc snippet. Note the shipped examples `print(result)` rather
than indexing it, so the repo's own gate has never seen the failure a user meets on line one.

## P2 — checkpoint errors are unwrapped, unlike every other store

```
StoreError: cannot open the event log at './events.sqlite3': file is not a database   # events
sqlite3.OperationalError: unable to open database file                                # checkpoint
```

The settings docs present four symmetric storage decisions. One wraps failures in a named, actionable
`StoreError`; the checkpointer leaks the raw driver exception.

## P2 — the CLI cannot operate what it can signal

Verified: the entire CLI is `agentdeck runs signal <run_id>` (`cli.py:33-36`). No list, no show, no
answer. "Operate it from a terminal" is roughly one-third true — an operator with only a shell can
send a cancel (which, per the P0 above, does nothing for a parked approval) but cannot see the inbox
or answer anything. Accurately documented in `concepts/protocols-and-surfaces.mdx`; still a real
capability gap. A read-only `runs pending` / `runs show` would close most of it.

## P3 — `answer()` accepts any value, silently

`deck.answer(run_id, value: Any)` does not validate against what the interrupted node expects.
Passing `12345` where the node compares `decision == "yes"` is silently treated as "no" — no error,
no warning, nothing in the docs saying validation is the caller's job. A workflow author cannot
distinguish "the operator declined" from "the operator's tooling sent garbage". D confirmed the docs
are silent with a grep proving absence.

## P0 — a tool that raises is invisible at every layer

C's second pass, with real credentials. A tool raising `RuntimeError("boom: test")` produces no
exception, no failed status, no HTTP error, and no `event: error` — on any path:

```
tool.call.completed  tool='explode'
    result_preview='An error occurred while running the tool. Please try again. Error: boom: test'
    error=None
run.completed
```

`status_of() == "completed"`, HTTP 200 on both the streamed and non-streamed paths. The only trace is
that the model's prose happens to mention it — and the streamed run's model paraphrased "boom: test"
away entirely, which leaves no signal at all.

Two halves make it ours, both verified in source:

1. **`ToolCallCompleted.error` is a dead field.** It exists (`core/events.py:256`) and
   `_tool_call_completed` (`translate.py:92`) — the only place the event is ever constructed — never
   passes it. Nothing in the package populates it.
2. **`failure_error_function` appears nowhere in agentdeck.** The SDK default is inherited unchanged,
   and that default is what turns a raised tool into an ordinary successful result carrying error prose.
   Choosing it is configuration, and configuration is ours.

A database call that times out is the most common failure a real tool has. #243 and #244 are loud
failures; this one is silent, which is why C ranked it above everything in its own first-pass top five.

## P2 — a non-serializable tool return is `repr()`'d into the log and the model's context

`str(item.output)` at `translate.py:94` coerces `object()` to `<object object at 0x7f11f83c7010>`, and
`result_size`/`result_sha256` are computed over that — so two identical tool results record as different,
and neither value means anything. The repr reaches the model's context unconditionally on every path;
whether it reaches the user's screen is model-dependent (the Python run echoed it verbatim, two HTTP runs
paraphrased it away). Contrast `ImageBlock`, which refuses an oversized block at construction with a
`ValidationError` naming the cap and the fix.

## P1 — the 500 contract only covers `AgentdeckError`, so engine failures return bare text

C never got credentials, pointed `OPENAI_BASE_URL` at a socket that accepts and never answers, and used
that to make turns genuinely in-flight. On the non-streamed chat path an engine failure returns:

```
Internal Server Error
HTTP_STATUS:500
```

No JSON, no `detail` — while 404/409/422 all answer `{"detail": ...}` and the **streamed** path handles
the identical failure exactly as documented (`event: error`, `data: {"error": "OpenAIError"}`). The
handler at `serve.py:143` is registered for `AgentdeckError` only; `OpenAIError`, a tool's `ValueError`,
`httpx` errors match nothing and fall through to Starlette's default. `compat.py`'s streamed frames catch
bare `Exception`, which is why the two paths disagree.

**Our golden suite structurally cannot see this**: `boom_flow`'s node raises `SkillError` — an
`AgentdeckError` — the one class the handler covers. So `12_workflow_error.http` pins the correct JSON
500 while the entire non-agentdeck class goes unrecorded. This is a deviation *from* the frozen wire, not
a request to change it.

## P2 — a killed worker holds its session for an hour by default

SIGKILL a worker mid-turn and the session is refused for `AGENTDECK_RUNTIME_STALE_RUN_AFTER_SECONDS`,
default **3600** (`_DEFAULT_STALE_RUN_AFTER = timedelta(hours=1)`). A fresh process retrying gets
`SessionBusyError` naming a run that no longer exists, and the only documented exits are waiting the hour
out or having lowered the setting *before* the crash. One crashed pod locks one user out of one
conversation. The takeover mechanism itself is correct and well-narrated once the window passes — the
default and the missing operator trigger are the problem.

## P2 — `agentdeck-serve --help` crashes

The first command after `pip install "agentdeck[serve]"` produces a `FileNotFoundError` about a missing
`./.agentdeck`. `main()` (`serve.py:313`) takes no argv at all, so the flag reaches no parser and startup
runs instead. The sibling `agentdeck` script has a real `--help`.

## P2 — the HTTP surface takes a string where the Python API documents blocks

`POST /agents/{name}/chat` refuses a content-block list (`422 message must be a string, got list`) while
`reference/deck.mdx` lists five block types under *What `input` accepts* for the same underlying
`run`/`stream`. Correct and deliberate — the v1 body is frozen — but `guides/serve-over-http.mdx` never
says so, so images and audio look supported over the wire. C recorded parity as an explicit expectation
in `plan.md` and found the gap only by testing.

## P2 — `input: Any` defeats the checker on the way in, too

`deck.run("HandoverBot", 12345, session_id="x")` passes pyright with zero errors and always raises
`TypeError` at runtime. Same method as the return-type finding, same cause: `Any` in, `| Any` out.
`coerce_input` already defines the accepted shape (`str | list[ContentBlock]`), so the annotation can
state it with no behavior change.

## What C confirmed works, under real adversarial pressure

Two genuinely concurrent turns on one session → `409` with the documented message. Unknown agent → `404`
naming the available ones. Malformed JSON, missing field, wrong-typed `session_id`, oversized
`ImageBlock` → four distinct, actionable messages, all before any model call. A typo'd kwarg → `TypeError`
suggesting the correct name, flagged by pyright too. Unknown skill → `ConfigError` listing every valid
one. And a turn that died before reaching the model still closed its log cleanly (`run.started` →
`run.failed`, no dangling `running`) and released its session immediately.

## The wayfinding finding (round 1's real contribution)

Round 1's reviewers filed ~12 "docs gaps"; **at least six were answered on pages neither opened** —
SKILL.md frontmatter (`skills.mdx:19`), `SessionBusyError` (`sessions-and-memory.mdx:81`),
`status(run_id)` (`deck.mdx:256`), `PendingRun`/`TurnResult` fields (`deck.mdx:236`), the sqlite path
rule, the two-store split. Neither opened `reference/` once in 40 minutes of asking questions it
answers.

Round 2 is the control: D read the concept pages during recon, predicted the store trap and the CLI's
scope *before building*, and reported **no wayfinding failures at all**. Same docs, opposite outcome —
so the pages are fine and the entry path is what fails. Two cheap levers:

1. **Errors should name the page.** `ConfigError: … missing a 'description' in its frontmatter — see
   /concepts/skills` turns a six-minute detour into twenty seconds. Every wall round 1 hit had its
   answer on one known page.
2. **Getting-started needs next-steps links** to skills, sessions, and the reference.

## Not confirmed

- *`control.requested` is promised for all runs in `run-control.mdx:28`* (D, P-none). The table does
  present it as the first phase of a controlled run without carving out workflows, and no such event
  is ever written for one — but the same page states the safe-point carve-out for *effect*. Real, but
  it is the P0 above wearing a docs hat; fix the behavior and the table becomes true.
- *Base install pulls `uvicorn`/`starlette`/`redis` transitively despite `serve` being an extra* (D,
  `[STATIC]`, cosmetic). Not re-verified; nothing base-install exposes uses them.
- *Suppress the `memory://` startup warnings* (A). Rejected — they are correct, and the P0 default
  trap is exactly what they exist for. D independently praised them as good UX.
- *Document the event-log sqlite schema* (B). Rejected — reaching the log through the API is the
  contract; documenting tables would freeze an internal.

## What both rounds independently praised

Discovery with no registration ceremony; strict, clear build validation; `interrupt()`; LangGraph
integration with no impedance mismatch; a real event log (both dumped a complete lifecycle by hand);
error messages that name the fix (`SessionBusyError`, `NotFoundError`, the durability `ImportError`,
`StoreError`, missing `thread_id`); and the fact that a durable, human-in-the-loop, multi-node
workflow needs **zero model credentials** to build and run. D also proved the documented
interrupt-purity rule empirically: a side effect placed before `interrupt()` fired **twice**, exactly
as the docs warn.

## Verified live, once credentials arrived (C, second pass)

Everything the first pass had to leave blocked now ran for real, and most of it passed:

- **Tools end to end** — `lookup_shift` returned the right shift from `shifts.json`; `file_handover_note`
  took `handover_notes.json` from nonexistent to real content on disk. Right tool, right arguments,
  first try.
- **Streaming** — `run.started → tool.call.started → usage.reported → tool.call.completed → text.delta
  ×2 → usage.reported → message.completed → run.completed`. Real deltas, documented order, a tool call
  folded into the same stream.
- **Skills actually change behavior** — the model called `load_skill('shift-notes')` before doing
  anything else and then followed the skill's structure. Not just build-time validation.
- **Run control on a live turn is fully correct** — `pause()` mid-stream produced
  `control.requested → control.observed → run.paused` with generated text preserved; `resume()`
  completed it; `cancel()` on a paused run ended it `cancelled` rather than continuing. This is the
  contrast that isolates #229 to the `WAITING_HUMAN` path alone.
- **Session durability, both directions** — default in-process SQLite: a fresh process saw 0 items, and
  the model correctly said it didn't know. Real Redis (via `redislite`, since no system server was
  available): process A's 2 items were visible verbatim to a fresh process B before B ran anything. This
  upgrades the FR-6 claim from reasoned-from-source to reproduced.

One caveat C flagged honestly rather than filing as a defect: in the Redis restart test the model's
*answer* was wrong ("I don't have access to your name") even though a direct `get_items()` dump proved
the replayed history contained it. That is the model's recall on replayed history, not agentdeck's
persistence — worth remembering if it resurfaces as a bug report.

## Still unverified

- **Kill mid-node** (not at the interrupt) — D could not build a kill window without injecting a sleep
  into the node, and declined to fake it.
- **Pause/cancel on a live agent turn**, the one case where the docs say a safe point *is* reached.
- **Postgres and Redis backends** — only sqlite and `memory://` were exercised.

Everything in the findings above was either reproduced without credentials or verified in source. The
credentials gap costs coverage, not confidence.

## Filed

All 16 carry the `finding` label; the six reproduced defects also carry `bug`.

| issue | sev | what |
|---|---|---|
| #250 | P0 | a tool that raises completes the run; `tool.call.completed.error` is never set |
| #251 | P2 | a non-serializable tool return is `repr()`'d into the log and the model's context |
| #229 | P0 | cancel against a `WAITING_HUMAN` run: accepted, never honored, no trace |
| #243 | P1 | the 500 contract covers only `AgentdeckError`; engine failures return bare text |
| #230 | P0 | `build()` is silent when the store pairing breaks the approval inbox |
| #232 | P1 | default checkpoint needs `[durability]`, so a default install can't run `durable=True` |
| #231 | P1 | `run()`'s `TurnResult \| Any` (and `input: Any`) defeat a type checker |
| #240 | P1 | human-approval never warns that the default event store empties `pending()` |
| #244 | P2 | a killed worker holds its session for an hour by default, with no release |
| #233 | P2 | checkpointer failures leak raw driver exceptions instead of `StoreError` |
| #234 | P2 | the CLI has no read path |
| #245 | P2 | `agentdeck-serve --help` crashes |
| #246 | P2 | serve-over-http never says `message` is string-only |
| #238 | P2 | errors name the problem but never the doc page |
| #239 | P2 | getting-started dead-ends |
| #241 | P2 | add-a-tool omits `function_tool`'s provenance and the raising-tool contract |
| #242 | P2 | no shipped example includes a skill |
| #235 | P3 | `answer()` takes any value unvalidated |

Not filed separately: the default-pairing trap (evidence added to **#212**, which owns it and holds
today's ruling), `run-control.mdx`'s `control.requested` table (folded into #229), and the four round-1
claims that turned out to be documented.
