# agentdeck v3.0.0  -  outsider review, united report

**Date:** 2026-08-13 (the document carried none; taken from its first commit) · **Status:** closed,
findings filed. Independent reviewers in clean-room
workspaces holding only what a real early adopter gets  -  the built `agentdeck-3.0.0` wheel,
`README.md`, the public docs-site content, `examples/`. No repo access, no `docs/`, no CLAUDE.md, no
issues, no web. Every claim was re-verified against the tree before it was written down.

| | model | territory | outcome |
|---|---|---|---|
| A | haiku | agent surface | live half **blocked**, no API key  -  discovery/validation/HTTP startup only |
| B | haiku | workflow surface | ran end to end, no defects found |
| C | sonnet | agent surface + adversarial | key never arrived; **stubbed the model endpoint** and reproduced 6 findings anyway |
| D | sonnet | ops surface + adversarial | ran end to end, **7 defects, 1 of them P0** |

Round 2 added a required adversarial phase, expectations recorded in `plan.md` before coding,
evidence tags (`[REPRODUCED]`/`[STATIC]`/`[BLOCKED]`), a doc-open trail, and a rule that "the docs
don't cover X" needs a pasted grep proving absence. **The model mattered less than the adversarial
phase**  -  B and D built the same kind of workflow, and only the one told to break it found anything.

## Findings

All 16 carry the `finding` label; the six reproduced defects also carry `bug`.

| issue | sev | finding | evidence |
|---|---|---|---|
| #250 | P0 | A tool that raises produces no exception, no failed status, no HTTP error and no `event: error`; `ToolCallCompleted.error` is never populated (`translate.py:92`) and `failure_error_function` appears nowhere, so the SDK default turns a raised tool into a successful result carrying error prose. | reproduced live |
| #229 | P0 | `cancel()` against a `WAITING_HUMAN` run returns `True`, is never honored, and leaves zero trace: only `resume_run()` polls the control port and `_paused()` lists `PAUSED` only (`service.py:259-265,370`), while `answer()` never polls it at all. | reproduced end to end |
| #230 | P0 | `build()` is silent when the store pairing breaks the approval inbox  -  `AGENTDECK_CHECKPOINT` defaults durable and `AGENTDECK_EVENTS` defaults `memory://`, and `pending()` projects the inbox from the event store (`service.py:500`), so the default pairing parks a run where nothing can find it. | cross-confirmed, B and D |
| #240 | P1 | The human-approval example never warns that the default event store empties `pending()`. | reproduced |
| #232 | P1 | `AGENTDECK_CHECKPOINT` defaults to `sqlite://…` but the saver ships in `[durability]`, so a default install plus `durable=True` is an ImportError on first run  -  a default setting requiring a non-default install. | both B and D hit it |
| #243 | P1 | The 500 handler is registered for `AgentdeckError` only (`serve.py:143`), so an `OpenAIError`, a tool's `ValueError` or an `httpx` error returns bare `Internal Server Error` while 404/409/422 answer JSON and the streamed path handles the same failure as documented. | reproduced against a hung socket |
| #231 | P1 | `Deck.run` is annotated `-> TurnResult \| Any` (`deck.py:705`) and `input: Any`, so a checker rejects `paused["type"]` from the docs' own idiom (`concepts/workflows.mdx:100`) and accepts `deck.run("HandoverBot", 12345, …)`, which always raises. | pyright, both directions |
| #244 | P2 | A SIGKILLed worker holds its session for `AGENTDECK_RUNTIME_STALE_RUN_AFTER_SECONDS`, default 3600, and the only documented exits are waiting it out or having lowered it before the crash. | reproduced |
| #233 | P2 | Checkpointer failures leak `sqlite3.OperationalError` while the event log wraps the same class of failure in `StoreError`  -  asymmetric across four storage decisions the docs present as symmetric. | reproduced |
| #234 | P2 | The whole CLI is `agentdeck runs signal <run_id>` (`cli.py:33-36`)  -  no list, no show, no answer, so a shell-only operator can neither see the inbox nor answer it. | verified in source |
| #251 | P2 | `str(item.output)` (`translate.py:94`) `repr()`s a non-serializable tool return into the log and unconditionally into the model's context, and `result_size`/`result_sha256` are computed over that repr, so two identical results record as different. | reproduced |
| #245 | P2 | `agentdeck-serve --help` crashes with a `FileNotFoundError` about a missing `./.agentdeck`, because `main()` (`serve.py:313`) takes no argv and startup runs instead. | reproduced |
| #246 | P2 | `POST /agents/{name}/chat` refuses a content-block list while `reference/deck.mdx` lists five block types for the same `run`/`stream`; correct and deliberate (the v1 body is frozen) but `guides/serve-over-http.mdx` never says so. | reproduced |
| #238 | P2 | Errors name the problem but never the doc page; every wall round 1 hit had its answer on one known page. | see wayfinding |
| #239 | P2 | Getting-started dead-ends  -  no next-steps links to skills, sessions or the reference. | see wayfinding |
| #241 | P2 | `add-a-tool` omits `function_tool`'s provenance and the raising-tool contract. | |
| #242 | P2 | No shipped example includes a skill. | |
| #235 | P3 | `deck.answer(run_id, value: Any)` validates nothing, so `12345` where a node compares `decision == "yes"` is silently treated as "no" and an author cannot tell a decline from garbage. | grep proving the docs are silent |

Not filed separately: the default-pairing trap (evidence added to **#212**, which owns it and holds
today's ruling), `run-control.mdx`'s `control.requested` table (folded into #229), and four round-1
claims that turned out to be documented.

The store-pairing matrix behind #230 and #212, all reproduced across real process restarts:

| `AGENTDECK_CHECKPOINT` | `AGENTDECK_EVENTS` | parked approval after restart |
|---|---|---|
| durable sqlite *(default)* | `memory://` *(default)* | **invisible** to `pending()`; still resumable if you already know the `thread_id` |
| `memory://` | durable sqlite | **visible** in `pending()`, and `answer()` always raises `ValidationError`  -  the state was never checkpointed, so the graph re-hydrates empty |
| durable sqlite | durable sqlite | visible and answerable  -  the only pairing that delivers |
| `memory://` | `memory://` | nothing survives (not re-run; trivially true) |

Two of the four documented pairings produce an inbox that lies, in opposite directions, and neither
fails at startup when both URLs are already known. The prose is done  -  `examples/`, the concept page
and a startup warning all cover it, and both reviewers read them and still called it a footgun;
**the default is what's wrong.**

## The wayfinding finding  -  round 1's real contribution

Round 1's reviewers filed ~12 "docs gaps"; **at least six were answered on pages neither opened**  -
SKILL.md frontmatter (`skills.mdx:19`), `SessionBusyError` (`sessions-and-memory.mdx:81`),
`status(run_id)` (`deck.mdx:256`), `PendingRun`/`TurnResult` fields (`deck.mdx:236`), the sqlite path
rule, the two-store split. Neither opened `reference/` once in 40 minutes. Round 2 is the control: D
read the concept pages during recon, predicted the store trap and the CLI's scope before building,
and reported no wayfinding failures at all. The pages are fine; the entry path fails.

## Not confirmed

| claim | verdict |
|---|---|
| `control.requested` is promised for all runs (`run-control.mdx:28`) | Real, but it is #229 wearing a docs hat  -  fix the behavior and the table becomes true. |
| Base install pulls `uvicorn`/`starlette`/`redis` transitively | Not re-verified, cosmetic; nothing base-install exposes uses them. |
| Suppress the `memory://` startup warnings | Rejected  -  they are correct, and the default trap is what they exist for. D praised them. |
| Document the event-log sqlite schema | Rejected  -  reaching the log through the API is the contract; documenting tables would freeze an internal. |

## What both rounds praised

Discovery with no registration ceremony; strict, clear build validation; `interrupt()`; LangGraph
integration with no impedance mismatch; a real event log both reviewers dumped by hand; error
messages that name the fix (`SessionBusyError`, `NotFoundError`, the durability `ImportError`,
`StoreError`, missing `thread_id`); and a durable human-in-the-loop workflow that needs **zero model
credentials** to build and run. D also proved the interrupt-purity rule empirically  -  a side effect
placed before `interrupt()` fired twice, exactly as the docs warn.

## Verified live once credentials arrived (C, second pass)

| area | result |
|---|---|
| Tools end to end | Right tool, right arguments, first try; `file_handover_note` wrote real content to disk. |
| Streaming | `run.started → tool.call.started → usage.reported → tool.call.completed → text.delta ×2 → usage.reported → message.completed → run.completed`, documented order, tool call folded in. |
| Skills change behavior | The model called `load_skill('shift-notes')` first and then followed the skill's structure. |
| Run control on a live turn | `pause()` mid-stream gave `control.requested → control.observed → run.paused` with text preserved, `resume()` completed it, `cancel()` on a paused run ended it `cancelled`  -  the contrast that isolates #229 to `WAITING_HUMAN`. |
| Session durability | Default in-process SQLite: a fresh process saw 0 items. Real Redis: process A's 2 items were visible verbatim to a fresh process B, upgrading FR-6 from reasoned-from-source to reproduced. |

One caveat C flagged rather than filing: in the Redis restart test the model *answered* "I don't have
access to your name" even though `get_items()` proved the replayed history contained it  -  the model's
recall on replayed history, not agentdeck's persistence.

## Still unverified

- **Kill mid-node**, not at the interrupt  -  D could not build a kill window without injecting a
  sleep into the node, and declined to fake it.
- **Pause/cancel on a live agent turn**, the one case where the docs say a safe point *is* reached.
- **Postgres and Redis backends**  -  only sqlite and `memory://` were exercised.
