# Documentation gaps

Behavior exposed by the current public API that the documentation does not adequately cover,
ordered by developer impact.

Traced against `v6.0.3` (`b9a7f76`). "Impact" is what it costs a developer who does not know:
**Silent** = wrong behavior with no error, **Runtime** = an exception they can search for,
**Friction** = time lost, **Blocked** = cannot complete the task from the docs at all.

---

## Tier 1: silent wrong behavior in production

Nothing raises. The log says nothing went wrong.

### G-01 Resuming a paused agent turn replays it, and its tools can run twice

| | |
|---|---|
| **Impact** | Silent. Duplicate side effects: a second email, a second charge, a second row. |
| **API** | `Run.resume()`, `Run.pause()`, any `@tool` an agent may call |
| **Truth** | `runtime/service.py:376-380`: "the engine is re-entered rather than un-suspended ... the run is played again from its own `run.started` input with the log as history ... a step a paused turn already took can be taken twice, so tool side effects must tolerate being called again." Also `openai_agents/executor.py:139-141`. |
| **Documented** | Nowhere. `reference/deck.mdx:365` promises "what a resume replays" and links to a page that never mentions replay. `workflows.mdx:26` states the opposite rule for workflows, which a reader generalizes. |
| **Where it belongs** | A new Execution page, as a two-row table (workflow resume continues, agent resume replays), plus one line on `tools.mdx`. |

### G-02 `cancel()` and `pause()` are ignored by a native body with no `ctx.safepoint()`

| | |
|---|---|
| **Impact** | Silent. `cancel()` returns cleanly and the run completes anyway. |
| **API** | `Run.cancel()`, `Run.pause()`, `@workflow`, `@tool` |
| **Truth** | `native/executor.py:192-212`: an ASYNC native body gets no checkpoint; a THREAD body gets one cancel-only checkpoint *after* the worker returns. Reproduced: a `@workflow` awaiting `asyncio.sleep(3)` with no `safepoint()` ignored `cancel()` and finished `completed`. |
| **Documented** | `lifecycle-and-control.mdx:70-72` lists three safe points ("between stream items, before dispatching a tool, at a node boundary") that describe only the agent executor. "Node boundary" is residue from the removed graph engine. `workflows.mdx:67` shows `ctx.safepoint()` as an optional pause point, not as the precondition for control. |
| **Where it belongs** | Execution page, per-executor safe-point table. `workflows.mdx` should say: without one of these, pause and cancel never land. |

### G-03 A sync `@tool` run as its own run cannot be cancelled in flight

| | |
|---|---|
| **Impact** | Silent. Follows from G-02 and cannot be worked around. |
| **API** | `@tool` with a `def` body, `ctx.safepoint()` |
| **Truth** | `core/context.py:218-224`: `safepoint()` on a THREAD body raises `ConfigError`. `native/executor.py:203-210`: the only checkpoint is after the worker call returns. |
| **Documented** | `context.mdx:76,82-83` says `safepoint()` is "yes, async body only" and raises otherwise. The consequence is never drawn. |
| **Where it belongs** | One sentence beside the capability table: a sync body has no await point, so a run of one is uncancellable until it returns. |

### G-04 `pause()` immediately followed by `resume()` leaves the run paused forever

| | |
|---|---|
| **Impact** | Silent. The run holds its session for `AGENTDECK_RUNTIME_STALE_RUN_AFTER_SECONDS` (one hour by default). |
| **API** | `Run.pause()`, `Run.resume()` |
| **Truth** | `core/status.py:141`: `RUNNING` + `RESUME` is `Verdict.NO_OP`. `pause()` records intent and returns before the run stops. Reproduced: `after pause+resume, status: running` then `settled status: paused`. |
| **Documented** | The opposite, in four places (`quickstart.mdx:92`, `pause-resume.mdx:14`, `lifecycle-and-control.mdx:62`, `README.md:131`). Nothing anywhere says how to observe a pause landing. |
| **Where it belongs** | `lifecycle-and-control.mdx`, once: poll `status()` until `PAUSED`, or watch for the `run.paused` event. Then fix the four copies. |

### G-05 `deck.workflows` mutation is silently accepted and discarded

| | |
|---|---|
| **Impact** | Silent. |
| **API** | `Deck.workflows` |
| **Truth** | `deck.py:663` returns a fresh dict comprehension. Reproduced: the write succeeded and `list(d.workflows)` was unchanged. `deck.agents` is a `MappingProxyType` and raises correctly. |
| **Documented** | `reference/deck.mdx:100` says both raise. |
| **Where it belongs** | Fix the API (`MappingProxyType`), and the sentence becomes true. |

---

## Tier 2: capability claims the implementation refuses

### G-06 A suspended `@workflow` does not survive its process

| | |
|---|---|
| **Impact** | Blocked, discovered at the first deploy. |
| **API** | `ctx.ask()`, `Run.answer()`, `deck.runs.get()` |
| **Truth** | `native/executor.py:105-108`: "a parked body lives in this process and this executor instance. Surviving a restart is the durable replay model, which is deferred." The refusal is `ConfigError("this run is not parked on anything: nothing is waiting to be answered.")`. |
| **Documented** | Correctly at `reference/deck.mdx:600-603` and `workflows.mdx:72-74`. Contradicted at `README.md:166` ("approvals that outlive the process that asked for them"), `README.md:140`, `llms.txt:16`, and `examples/chat-in-the-terminal/README.md:41-43`. |
| **Where it belongs** | The human-input page, first paragraph. Also fix the error message: "not parked on anything" reads as a state error, not as "the process that parked this run is gone". |

### G-07 No binding can supply a per-request context

| | |
|---|---|
| **Impact** | Blocked. The whole served path is unavailable to any deck whose callables need `ctx.data`. |
| **API** | `DeckGateway.start()`, `deck.serve()`, `deck.asgi()`, `Deck(context=...)` |
| **Truth** | `bindings/gateway.py:158-166` has no `context` parameter. `examples/jack/jack/server.py:10-22` writes its own route rather than use `asgi()`, and says why. |
| **Documented** | Correctly at `reference/deck.mdx:578-583`, `native.mdx:65`, `context.mdx:111`, `known-issues.mdx:64` (#227). Contradicted by the landing page hero (`index.mdx:22-30`), which serves a `context=DocsCorpus` deck. |
| **Where it belongs** | Fix the landing page. Reclassify #227 out of "rough edges: just things that will cost you a few minutes". |

### G-08 Cross-process pause, cancel and the CLI need `AGENTDECK_CONTROL=sqlite://`

| | |
|---|---|
| **Impact** | Blocked, and the page that claims it is the page that omits it. |
| **API** | `Run.pause()`, `Run.cancel()`, `agentdeck runs signal` |
| **Truth** | `runtime/settings.py:382-388`: with the `memory://` default "a signal written in one process is invisible to another, so ... the `agentdeck runs signal` CLI and a second web worker cannot reach a run at all." |
| **Documented** | Well on `operate/deployment.mdx`. **Absent** from `pause-resume.mdx`, whose subtitle is "resume them safely across processes or workers", and from `reference/cli.mdx`, which documents `--control-db` without saying a default deck writes nowhere that file can see. |
| **Where it belongs** | First paragraph of whichever page owns pause/resume, and one line on `reference/cli.mdx`. |

### G-09 Cross-process run recovery needs a durable `AGENTDECK_EVENTS`

| | |
|---|---|
| **Impact** | Friction, then Blocked. |
| **API** | `deck.runs.get()`, `deck.runs.list()` |
| **Truth** | `runtime/settings.py:375`: `memory://` is the default, "in-process, gone when the process exits". |
| **Documented** | `runs.mdx:61-65` has the callout. `mental-model.mdx:53`, `overview.mdx:26` and `README.md:140` state the capability unqualified. |
| **Where it belongs** | In the claim, not in a separate callout. |

### G-10 An agent's `@tool` cannot request human approval

| | |
|---|---|
| **Impact** | Blocked, and the docs advise it. |
| **API** | `ToolCtx` (no `ask`), `WorkflowCtx.ask` |
| **Truth** | `ask` is `WorkflowCtx`-only (`core/context.py:445`), and a `@tool` declaring `WorkflowCtx` is a `build()` error (`native.py:139-156`). The supported shape is a workflow that asks, then invokes the agent. |
| **Documented** | `examples/chat-agent-with-a-tool.mdx` says "Keep anything destructive behind [human approval]" and links to the 16-line stub. The workflow-gates-the-agent pattern appears nowhere. |
| **Where it belongs** | One worked example on the human-input page. This is the right architectural line, it just has to be said. |

---

## Tier 3: undocumented ceilings and error semantics

### G-11 `await run` raises a bare `RuntimeError` for cancelled and rehydrated-failed runs

| | |
|---|---|
| **Impact** | Runtime, uncatchable by the documented pattern. |
| **API** | `await run` |
| **Truth** | `deck.py:1591-1600`. Reproduced: `RuntimeError: run '...' was cancelled: bye` and `RuntimeError: run '...' failed: ValueError in engine 'native'`. A `FAILED` run this process executed raises the engine's own exception instead, so the same `await` raises two different types depending on which process asks. |
| **Documented** | `reference/deck.mdx:372-386` covers `RunSuspendedError` only. `troubleshooting.mdx:13` promises "every one is an `AgentdeckError`, so one `except` catches the lot". |
| **Where it belongs** | Better as two taxonomy classes than as a paragraph. Either way the local-vs-rehydrated asymmetry needs stating. |

### G-12 `run.answer()` raises `InputError`, not `ValueError`

| | |
|---|---|
| **Impact** | Runtime, and `except ValueError` misses it. |
| **API** | `Run.answer()` |
| **Truth** | `core/errors.py:53`: `InputError(AgentdeckError)`. Reproduced on both refusal paths (non-JSON value, and a value outside `options`). |
| **Documented** | `workflows.mdx:52` says `# ValueError`. `Run.answer`'s own docstring (`deck.py:1506`) says `ValueError` too. `whats-new-6.mdx:36` records the v6 rename that invalidates both. |
| **Where it belongs** | Fix both, and assert the raised type against the documented one in a test. |

### G-13 There is no run timeout

| | |
|---|---|
| **Impact** | Friction, then an operational surprise. |
| **API** | `await run`, `deck.run()` |
| **Truth** | `deck.py:1549`: "there is no timeout parameter to wait either out, and the caller who wants one polls `status()`/`pending()` instead of blocking forever". Deliberate. |
| **Documented** | The suspended case is (`reference/deck.mdx:386`). That there is no timeout for a *running* run, and that bounding one is caller-side (`asyncio.wait_for` around `await run`, plus `run.cancel()`), is nowhere. |
| **Where it belongs** | Execution page. This is a design decision worth stating, not a gap to apologize for. |

### G-14 `AGENTDECK_RUNNER_MAX_TURNS` defaults to 30

| | |
|---|---|
| **Impact** | Runtime, on a long agent loop. |
| **API** | `Deck.run()` on an agent |
| **Truth** | `reference/settings.mdx`: "Maximum turns `Runner.run`/`run_streamed` may take before giving up." |
| **Documented** | The generated settings table only. No narrative page mentions a turn ceiling, or what the run looks like when it is hit. |
| **Where it belongs** | `agents.mdx` or the Execution page, with the resulting `run.failed` shape. |

### G-15 Concurrent sync `@tool` bodies are capped at `min(32, cpu_count + 4)`, unconfigurably

| | |
|---|---|
| **Impact** | Friction under load; requests queue with no signal. |
| **API** | any sync `@tool` |
| **Truth** | `core/workers.py:25`: "`max_workers=None` keeps Python's own default (`min(32, cpu_count + 4)`); nothing adds a settings knob for it." |
| **Documented** | `tools.mdx:15` says "a bounded worker pool the deck owns" and names neither the bound nor that it is fixed. |
| **Where it belongs** | One sentence on `tools.mdx`: the bound, and that an async body avoids it. |

### G-16 A run that fires many reports closes proportionally slower

| | |
|---|---|
| **Impact** | Friction; a latency characteristic of a public API. |
| **API** | `ctx.reporter.*` |
| **Truth** | `CHANGELOG.md` 6.0.3: "That wait is one store write per pending report: a run that fires thousands of reports takes thousands of writes to close, and its completion arrives that much later." |
| **Documented** | Changelog only. `context.mdx` §Reporting covers lifetime and failure but not cost or ordering. |
| **Where it belongs** | An Observability page, beside the ordering caveat in `core/reporting.py:32-33` (a consumer reading the runtime generator still sees a report at the engine's next payload, `#487` item 2). |

### G-17 `UnsupportedControlError` cannot be raised by any shipped configuration

| | |
|---|---|
| **Impact** | Friction in the wrong direction: readers defend against an impossible error. |
| **API** | `Run.pause()`, `Run.cancel()`, `Run.resume()`, HTTP `501` on the Native control routes |
| **Truth** | Path A needs a non-suspendable executor; all three declare `suspendable = True`, and recovered handles hardcode `_RECOVERED_SUSPENDABLE = True` (`deck.py:1349`). Path B needs no control port; `composition.resolve_control_port` always returns one or raises. |
| **Documented** | As a real hazard on `reference/run.mdx:26-29`, `troubleshooting.mdx:40` and `reference/deck.mdx:346`, all naming the unreachable cause. |
| **Where it belongs** | Keep the class for `agentdeck#337`; stop teaching it until something raises it. `run.can` already answers the user-facing question. |

### G-18 `Exposure` opens the Deck itself when it is not already open

| | |
|---|---|
| **Impact** | Friction: a reader cannot tell whether `async with deck` is required before `serve`. |
| **API** | `deck.serve()`, `deck.asgi()`, `deck.expose()` |
| **Truth** | `exposure.py:39-41`: `owns_deck = not self._deck.is_open`, and it closes what it opened. |
| **Documented** | `bindings/serve.mdx:13-16` says `serve` "opens the Deck". Whether it does so for an already-open deck, and who closes it, is unstated. Every serve example omits `async with`, which is correct and unexplained. |
| **Where it belongs** | One clause on `bindings/serve.mdx`. |

---

## Tier 4: whole capabilities with no page

### G-19 Handoffs, subagents and hooks

| | |
|---|---|
| **Impact** | Blocked. |
| **API** | `Agent(handoffs=..., subagents=..., hooks=..., handoff_description=..., model_settings=..., base=...)`, `AgentDeclaration`, `instructions=` as a callable |
| **Truth** | `authoring/agent.py:50-155`. `subagents=` has real semantics: each name becomes a tool the model may call, the child runs as its own run, and the result returns rather than transferring the conversation (`agent.py:67-71`). |
| **Documented** | `agents.mdx` covers 5 of the 13 constructor parameters (`name`, `instructions`, `model`, `tools`, `output_type`). `handoffs=` and `subagents=` get one passing clause in `mental-model.mdx:25`. `runs.mdx:84-96` documents the `agent.changed` event a handoff emits without ever showing how to declare one. |
| **Where it belongs** | A new page: handoff vs delegation vs `ctx.invoke`, absorbing the `agent.changed` section. AgentDeck is described as a multi-agent harness; this is the multi-agent part. |

### G-20 Skills

| | |
|---|---|
| **Impact** | Blocked: a reader cannot write a `SKILL.md` from the docs. |
| **API** | `Skills(*roots, validate=True)`, `Agent(skills=[...])`, the generated `load_skill` tool, `SkillError` |
| **Truth** | `skills/__init__.py:25-40`: at least one root required; `validate=True` enforces that a `SKILL.md`'s frontmatter `name` matches its directory name and that `description` is non-empty, both as `build()` failures. `reference/deck.mdx:591-594`: a skill never receives a context. |
| **Documented** | 13 lines, no code, no frontmatter format, no `Skills`. The only working demonstration is `examples/agent-with-a-skill/`. |
| **Where it belongs** | The existing page. The two `build()` rules are exactly what a first attempt gets wrong. |

### G-21 MCP

| | |
|---|---|
| **Impact** | Blocked. |
| **API** | `Deck(mcp=...)`, `MCP`, `McpServerSettings`, `Agent(mcp=[...])`, `.mcp.json`, `AGENTDECK_MCP_SERVERS` |
| **Truth** | `agentdeck/mcp.py`; `from_project` reads `.mcp.json` from the project root's **parent** (`deck.py:642`), a placement no page states. `build()` registers server specs without connecting (`deck.py:726-729`); `__aenter__` connects. |
| **Documented** | `integrations/mcp.mdx` is 12 lines with no code. `reference/deck.mdx:51` mentions `mcp=".mcp.json"` in a signature sketch. |
| **Where it belongs** | The existing page, including the sibling-of-`.agentdeck/` path rule and the build-vs-open split. |

### G-22 Adopting existing agents

| | |
|---|---|
| **Impact** | Blocked, and the quickstart links here. |
| **API** | `Agent(handoffs=[sdk_agent])`, `function_tool` passthrough in `tools=`, `_named_mapping`'s refusal of a bare SDK agent in `agents=` |
| **Truth** | `deck.py:418-427` refuses an SDK agent in `agents=` with a message naming the alternative: "An Agents SDK agent is legitimate as a handoff target instead: `Agent(name=..., handoffs=[...])`." `tools.mdx:79-91` covers the tool half well. |
| **Documented** | `integrations/existing-agents.mdx` is 12 lines: "Bring your custom agent implementations and wrap them with AgentDeck's runtime." `quickstart.mdx:192` promises "Wrap OpenAI Agents SDK agents into a Deck". |
| **Where it belongs** | The existing page. The error message already contains the answer. |

### G-23 The Python API reference omits five public modules

| | |
|---|---|
| **Impact** | Friction, repeatedly. |
| **API** | `agentdeck.errors` (12 classes), `agentdeck.observers` (3 classes + `instrument_agents_sdk`), `agentdeck.testing` (3 names), `agentdeck.views` (7 members), `agentdeck.skills.Skills`, `agentdeck.mcp.MCP`, plus `AgentDeclaration`, `NativeDefinition`, `Controls`, `InterruptResult`, `deck.session_for`, `deck.settings` |
| **Truth** | All public. `deck.settings` in particular is the compliant replacement for the `get_settings()` that `examples/jack/jack/server.py:41` uses and `agentdeck/README.md` discourages. |
| **Documented** | `reference/python-api.mdx` lists `agentdeck/__init__.py` and `agentdeck.bindings`, its only two `docs_sources`. That is exactly why the rest accumulated: `scripts/docs_impact.py` never flags a change it is not mapped to. |
| **Where it belongs** | Widen the page's `docs_sources` to every public module, which makes `test_docs_impact.py` enforce this instead of leaving it to notice. |

### G-24 Reporter, Observer and views have no shared home

| | |
|---|---|
| **Impact** | Friction: "how do I report progress" is not guessable from the navigation. |
| **API** | `ctx.reporter`, `Deck(observers=[...])`, `agentdeck.views` |
| **Truth** | Three halves of one story: reports out, observers in, views filtering between them (`runtime/dispatch.py` does the filtering, not the observer). |
| **Documented** | Reporter is a subsection of `build-your-deck/context.mdx`. Observers are a section of `reference/deck.mdx`. `views` is named on that one page and nowhere else. |
| **Where it belongs** | One Observability page. |

---

## Tier 5: verification and hygiene

### G-25 62 of 83 doc fences are verified only to "it parses"

| | |
|---|---|
| **Impact** | This is the mechanism behind most of Tier 1 and Tier 2. |
| **Truth** | Measured at this tag: 8 `run`, 10 `no-test`, 2 `illustrative`, 1 `file=`, 62 bare. Zero executed fences on the quickstart or anywhere under `runs-and-control/`. `tests/test_docs_examples.py` passes: "8 run, 2 illustrative". |
| **Where it belongs** | The machinery already exists and is good. Make the quickstart's fences `run` fences first: that alone catches the missing `asyncio.run(main())` and the wedging pause/resume. The end state is that a bare ```` ```python ```` fence needs a `reason=`, the same way `no-test` does today. |

### G-26 Known Issues is pinned to v6.0.0

| | |
|---|---|
| **Impact** | Friction, and it erodes the page's own premise. |
| **Truth** | `known-issues.mdx:17` says "open against **v6.0.0**"; `CHANGELOG.md` records 6.0.1 through 6.0.3, including `#487` (reports silently dropped from a 64-deep buffer), `#470` (a committed claim that never reached the play left a run `RUNNING` with nobody playing it) and `#471` (a completed run reading back as cancelled). All three are the page's own silent-failure class. |
| **Where it belongs** | The page argues that listing fixed things "teaches you to distrust the entries that are still true". Omitting three releases of fixes teaches the same distrust. |

### G-27 Source-level residue from removed architectures

| Location | Residue |
|---|---|
| `core/context.py:171`, `core/context.py:470` (an error message), `authoring/native.py:87` | name `approve()`, which `core/context.py:323` says does not exist |
| `observers.py:12` | "the settings model already layers init/env/YAML"; v5 removed the YAML source (`migration-guides.mdx:120-129`), and `runtime/settings.py` has no `yaml` |
| `cli.py:11` | "lives outside ``surfaces/``"; there is no `surfaces/` package |
| `cli.py:8` | names `Deck.runs.resume`; `Runs` has only `start`/`get`/`list` |
| `cli.py:12` | "There is no HTTP control route"; there are three (`native/binding.py:74-76`) |
| `CLAUDE.md` §2 | lists `agentdeck/surfaces/` as a layer |
| `lifecycle-and-control.mdx:71` | "at a node boundary", from the removed graph engine |

Impact is Friction for contributors and coding agents, plus one user-facing error message naming a
method that does not exist.

### G-28 Small accuracy items

| Item | Truth |
|---|---|
| `events.mdx:84` "all 22 kinds" | 21 unique `kind: Literal[...]` in `core/events.py`; `reference/events.mdx` lists 21 |
| `reference/deck.mdx:183` `"thread_id": ""` | `Run.pending()` returns the run id; `interrupts.py:19-24` has it right |
| `reference/run.mdx` | omits `run.id`/`key`/`namespace`/`session_id`, omits `events()`'s parameters and its suspension boundary, calls `await run` "the final output" |
| "Run Control" | a page title that does not exist, in `reference/deck.mdx:364`, `:598`, and the CLI `--help` |
| 14 pages | no frontmatter `title:`/`description:` |
| `native.mdx` route table | the run summary omits `key`, so a client that started a run with one cannot read it back |
| `lifecycle-and-control.mdx:87` | collapses `REFUSED` (raises) and `NO_OP` (silent) into one glyph, which is the difference that decides whether a `try` is needed |
| `quickstart.mdx:65-74` | omits the three stderr warnings a default `Deck` open writes |
| `examples/jack/jack/server.py:41,49` | imports `agentdeck.runtime.settings` and `agentdeck.core.ports`, both discouraged; `Observer` and `deck.settings` have public spellings |

---

## Priority order

| Rank | Gaps | Rationale |
|---|---|---|
| 1 | G-01, G-02, G-04 | silent wrong behavior on the SDK's headline capability, all three currently taught backwards |
| 2 | G-06, G-07, G-08, G-09 | four capability claims the implementation refuses, three of them in the README or on the landing page |
| 3 | G-25 | the mechanism that let rank 1 and 2 happen, with tooling that already exists |
| 4 | G-11, G-12 | error semantics a HITL or worker integration must get right |
| 5 | G-19, G-20, G-21, G-22 | four capabilities with no usable page, two of them linked from the quickstart |
| 6 | G-03, G-13, G-14, G-15, G-16, G-18 | undocumented ceilings and lifecycle details |
| 7 | G-05, G-17, G-23, G-24 | small API fixes and reference consolidation that each delete documentation |
| 8 | G-26, G-27, G-28 | staleness and hygiene |

One page would carry most of rank 1 and rank 6: an **Execution** page holding the per-executor
safe-point table, the resume-replay rule, the absence of a timeout, and the two concurrency
ceilings. It is the largest single gap in the information architecture, and its absence is why the
Lifecycle page is wrong rather than merely thin.
