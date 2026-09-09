# Example validation

Every meaningful code example on the documentation site, in the repository markdown, and in
`examples/`, traced against `v6.0.3` (`b9a7f76`).

**Method.** Each example was read against the implementing symbol. Where the outcome was not
decidable by reading, the snippet was executed in a subprocess against
`agentdeck.testing.scripted_model_server` with `AGENTDECK_EVENTS=memory://` and no external
services, the same harness `tests/test_docs_examples.py` uses. Rows marked **run** in the Evidence
column were executed. Nothing was executed against a real model provider.

**Status meanings.**

| Status | Meaning |
|---|---|
| VALID | imports, signatures, arguments, return values and async usage all match; no unstated precondition |
| VALID BUT MISLEADING | runs, but implies behavior the implementation does not guarantee |
| INCOMPLETE | omits setup, cleanup or a call needed to produce the documented effect |
| STALE | matched a previous release; a name, class or default has since changed |
| BROKEN | raises, or cannot produce the documented result |
| UNVERIFIED | needs a live model, a real server or a second process; not decidable here |

---

## 1. Docs site: quickstart and overview

| Example | Location | Status | Current API? | Problem | Confidence |
|---|---|---|---|---|---|
| install | `meet-agentdeck/quickstart.mdx:22` | VALID | yes | - | High |
| build a deck | `quickstart.mdx:30-40` | VALID | yes | - | High |
| start a run | `quickstart.mdx:46-55` | **INCOMPLETE** | yes | no `import asyncio`, no `asyncio.run(main())`; defines a coroutine and exits with no output. Adding both lines reproduces the documented output exactly (**run**) | High |
| expected output block | `quickstart.mdx:65-74` | VALID BUT MISLEADING | yes | event order and kinds verified correct (**run**), but omits the three stderr warnings a default `Deck` open writes | High |
| pause / resume / cancel callout | `quickstart.mdx:92-96` | **BROKEN** | yes | `resume()` is a `NO_OP` while status is `running`, so the run pauses and never resumes. Reproduced: `settled status: paused` (**run**) | High |
| `OPENAI_API_KEY` box | `quickstart.mdx:106-117` | VALID | yes | - | Medium |
| model-not-found box | `quickstart.mdx:124-133` | VALID | yes | - | Medium |
| never-awaited box | `quickstart.mdx:140-149` | VALID | yes | - | High |
| `SessionBusyError` box | `quickstart.mdx:156-164` | VALID BUT MISLEADING | yes | the page's own code passes no `session_id`, so this error cannot arise from it | High |
| deck-not-open box | `quickstart.mdx:171-182` | VALID BUT MISLEADING | yes | uses `deck.stream(...)`, never introduced on this page | High |
| `Deck(agents=[assistant], workflows=[approve])` | `mental-model.mdx:41` | VALID | yes | `approve` is undefined on the page; illustrative in intent, unmarked in fact | Medium |
| provider table | `meet-agentdeck/overview.mdx:22-28` | VALID BUT MISLEADING | yes | row 3 claims cross-process pickup with no mention of `AGENTDECK_EVENTS`; row 2 conflates approval with pause/resume | High |
| multi-binding serve | `whats-new-6.mdx:24` | VALID | yes | - | High |
| complete-setup serve | `whats-new-6.mdx:75-90` | VALID | yes | commented-out roadmap bindings are clearly marked | High |

## 2. Docs site: landing page

| Example | Location | Status | Current API? | Problem | Confidence |
|---|---|---|---|---|---|
| Jack hero snippet | `docs-site/content/index.mdx:22-30` | **BROKEN** | yes | serves a `context=DocsCorpus` deck. `DeckGateway.start()` has no `context` parameter (`bindings/gateway.py:158`), so all three of Jack's tools would receive `ctx.data is None`. `reference/deck.mdx:578-583` says "If a root needs a context, do not serve it"; `examples/jack/jack/server.py:10-22` explains that Jack cannot use `asgi()` for exactly this reason | High |

## 3. Docs site: Build Your Deck

| Example | Location | Status | Current API? | Problem | Confidence |
|---|---|---|---|---|---|
| declare an agent | `build-your-deck/agents.mdx:19-27` | VALID | yes | - | High |
| provider prefix | `agents.mdx:42-44` | VALID | yes | matches `runconfig.py:120-145` | High |
| `.env` sample | `agents.mdx:48-52` | VALID | yes | - | High |
| send an image | `agents.mdx:62-75` | UNVERIFIED | yes | correctly marked `no-test reason="needs an image file and a live model"` | High |
| `AgentOutputSchema` | `agents.mdx:96-113` | VALID | yes | - | Medium |
| `@tool` + `deck.run` | `build-your-deck/tools.mdx:20-43` | VALID | yes | executed by the suite as `run tool="lookup_order"`; the model is scripted to call the tool, so the body is proven to run | High |
| plain function tool | `tools.mdx:49-53` | VALID | yes | - | High |
| `@tool`-vs-plain table | `tools.mdx:60-66` | VALID | yes | matches `injection.py` refusal and `_invoked_name` | High |
| build() refusal text | `tools.mdx:74-77` | VALID | yes | - | Medium |
| tool returns a failure | `tools.mdx:98-106` | VALID | yes | `SHIFTS` undefined, but the point is the return shape | High |
| `ctx.ask` workflow | `build-your-deck/workflows.mdx:10-21` | VALID | yes | - | High |
| answer and await | `workflows.mdx:28-33` | VALID BUT MISLEADING | yes | `await run.answer(True)` immediately after `start` with no wait for `waiting_answer`; the comment implies the wait without showing it | High |
| options | `workflows.mdx:37-39` | VALID | yes | - | High |
| read pending options | `workflows.mdx:43-46` | VALID | yes | `pending["payload"]["options"]` confirmed (**run**) | High |
| refused answer | `workflows.mdx:51-54` | **BROKEN** | no | comment says `# ValueError, nothing resumed`; the raise is `InputError`, which is not a `ValueError` (`core/errors.py:53`). Reproduced (**run**) | High |
| `safepoint` batch | `workflows.mdx:61-70` | VALID BUT MISLEADING | yes | correct, but does not say that without `ctx.safepoint()` a pause or cancel never lands at all (**run**: a workflow with no safepoint ignored `cancel()` and completed) | High |
| `ctx.invoke` / `ctx.parallel` | `workflows.mdx:93-100` | VALID | yes | uses a catalog name and definitions, which `_invoked_name` accepts | High |
| input-binding table | `workflows.mdx:109-113` | VALID | yes | matches `_invocation_input` | High |
| Skills page | `build-your-deck/skills.mdx` | **INCOMPLETE** | n/a | 13 lines, no code at all; `Skills(*roots, validate=True)` and the two `SKILL.md` frontmatter rules that fail `build()` appear nowhere | High |
| context end-to-end | `build-your-deck/context.mdx:24-52` | VALID | yes | executed by the suite | High |
| reporter in a tool | `context.mdx:101-107` | VALID | yes | `Cache` undefined, but four methods and the sync call are right | High |
| capability table | `context.mdx:71-79` | VALID | yes | matches `ToolCtx` / `WorkflowCtx` | High |
| create a Deck | `build-your-deck/deck.mdx:14-20` | VALID | yes | - | High |
| `from_project` | `deck.mdx:24-26` | VALID | yes | - | High |

## 4. Docs site: Runs and Control

Zero executed fences on this whole section.

| Example | Location | Status | Current API? | Problem | Confidence |
|---|---|---|---|---|---|
| start and await | `runs-and-control/runs.mdx:20-24` | VALID | yes | - | High |
| start with a key | `runs.mdx:41-43` | VALID | yes | - | High |
| get / list | `runs.mdx:50-54` | VALID | yes | `RunStatus` unimported on the page; both `get` forms are correct | High |
| "works from another process" | `runs.mdx:56-65` | VALID | yes | **the only place on the site that states the `AGENTDECK_EVENTS` precondition** | High |
| follow | `runs.mdx:76-79` | VALID | yes | - | High |
| handoff events | `runs.mdx:90-94` | VALID | yes | payload fields match `AgentChanged` | Medium |
| continue a conversation | `runs-and-control/sessions.mdx:20-36` | VALID | yes | executed by the suite | High |
| enrich then answer | `sessions.mdx:53-76` | VALID | yes | executed by the suite | High |
| `SessionBusyError` | `sessions.mdx:89-109` | VALID | yes | executed by the suite | High |
| stream a run | `events.mdx:21-32` | VALID | yes | - | High |
| read back / `from_seq` | `events.mdx:44-52` | VALID | yes | - | High |
| `deck.stream` | `events.mdx:62-66` | VALID | yes | - | High |
| switch on kind | `events.mdx:72-77` | VALID | yes | payload fields match | High |
| "all 22 kinds" | `events.mdx:84` | STALE | no | 21 unique `kind: Literal[...]` in `core/events.py`; `reference/events.mdx` lists 21 | High |
| `RunStatus` import | `lifecycle-and-control.mdx:31-36` | VALID BUT MISLEADING | yes | uses `agentdeck.core.status`; `RunStatus` is exported from `agentdeck`, and `bindings/write-your-own.mdx:59` forbids `agentdeck.core.*` | High |
| control sequence | `lifecycle-and-control.mdx:59-68` | **BROKEN** | yes | `pause()` then `resume()` then `cancel()` then `answer()` cannot all be legal in sequence; the pause/resume pair wedges the run (**run**) | High |
| capability matrix | `lifecycle-and-control.mdx:80-89` | VALID BUT MISLEADING | yes | collapses `REFUSED` (raises) and `NO_OP` (silent) into one glyph, and says so; that difference decides whether a `try` is needed | High |
| pause / resume | `pause-resume.mdx:14-17` | **BROKEN** | yes | same wedging pair, plus the page subtitle claims "across processes or workers" with no mention that `AGENTDECK_CONTROL` defaults to `memory://` and cannot cross a process | High |
| answer a prompt | `human-input.mdx:14-16` | **BROKEN** | yes | `run.answer({"approved": True})` against the canonical `options=[True, False]` ask raises `InputError` (**run**). The page's only snippet | High |

## 5. Docs site: Bindings and Operate

| Example | Location | Status | Current API? | Problem | Confidence |
|---|---|---|---|---|---|
| multi-binding serve | `bindings/index.mdx:44-49` | VALID | yes | HTTP + stdio in one exposure works: `Exposure.serve` runs uvicorn and starts the stdio task in the ASGI lifespan (`exposure.py:99-109`) | High |
| standalone serve | `bindings/serve.mdx:21-27` | VALID BUT MISLEADING | yes | shows `host="0.0.0.0"` (also the default) with no note that the Native binding has no authentication; the warning is on `native.mdx:63` instead | High |
| `serve_async` | `serve.mdx:38` | VALID | yes | - | High |
| `asgi` + uvicorn | `serve.mdx:47-57` | VALID | yes | - | High |
| validation error table | `serve.mdx:64-72` | VALID | yes | all five messages match `exposure.py:121-154` verbatim | High |
| `Native.http` | `bindings/native.mdx:27-29` | VALID | yes | - | High |
| route table | `native.mdx:33-45` | VALID | yes | 10 routes and the run-summary shape match `native/binding.py:69-78,182-190`; `key` is absent from the summary and from the table | High |
| `Terminal.stdio` | `bindings/terminal.mdx:18-24` | VALID | yes | - | High |
| `AGUI.http` | `bindings/agui.mdx:28-33` | VALID | yes | - | High |
| plugin binding | `bindings/write-your-own.mdx:62-67` | UNVERIFIED | yes | correctly marked `no-test`; `agentdeck_myprotocol` is a placeholder | High |
| import-boundary table | `write-your-own.mdx:55-60` | VALID BUT MISLEADING | yes | forbids `agentdeck.core.*`, which two reference pages teach, and for which `KNOWN_KINDS`/`TERMINAL_KINDS` have no permitted alternative | High |
| `app.py` for uvicorn | `operate/deployment.mdx:~55` | VALID | yes | `Deck.from_project("./.agentdeck").asgi(Native.http())` | High |
| systemd unit | `deployment.mdx:71` | VALID | yes | binds `127.0.0.1`, which is the right default for a reverse-proxied service | High |
| durability warnings block | `deployment.mdx:~20` | VALID | yes | matches the runtime's real stderr output verbatim (**run**) | High |

## 6. Docs site: Reference

| Example | Location | Status | Current API? | Problem | Confidence |
|---|---|---|---|---|---|
| `.agentdeck` agent file | `reference/deck.mdx:21-25` | VALID | yes | `file=` fence, written into the temp project the next fence runs in | High |
| `from_project` + `run` | `reference/deck.mdx:27-42` | VALID | yes | executed by the suite | High |
| constructor sketch | `reference/deck.mdx:46-55` | VALID | yes | correctly marked `illustrative` | High |
| sequential decks | `reference/deck.mdx:84-92` | VALID | yes | - | High |
| `ContextTypeError` | `reference/deck.mdx:109-135` | VALID | yes | executed by the suite | High |
| `deck.agents`/`workflows` immutable | `reference/deck.mdx:100` | **BROKEN** | no | `agents` is a `MappingProxyType` and raises; `workflows` returns a fresh `dict` per call, so mutating it silently succeeds and is discarded. Reproduced (**run**) | High |
| `InterruptResult` shape | `reference/deck.mdx:183` | STALE | no | shows `"thread_id": ""` and says no executor produces one; `Run.pending()` returns the run id (**run**), which `interrupts.py:19-24` documents correctly | High |
| content blocks in | `reference/deck.mdx:~212-218` | UNVERIFIED | yes | marked `no-test` | High |
| `run.completed` output | `reference/deck.mdx:243-246` | UNVERIFIED | yes | marked `no-test` | High |
| `runs.start/get/list` | `reference/deck.mdx:309-316` | VALID | yes | marked `no-test`; all three forms correct | High |
| `Run` method table | `reference/deck.mdx:342-352` | VALID BUT MISLEADING | yes | `pause`'s `UnsupportedControlError` cause ("no control backend") is unreachable: `resolve_control_port` always returns one and all three executors are `suspendable = True`. `await run` row omits the `RuntimeError` for cancelled/failed | High |
| pause/resume/cancel | `reference/deck.mdx:354-361` | **BROKEN** | yes | the wedging pair again | High |
| `RunSuspendedError` | `reference/deck.mdx:377-384` | VALID | yes | `.status` and `.pending` both correct | High |
| observers | `reference/deck.mdx:452-506` | VALID | yes | three-state `observers=` rule matches `deck.py:513-517` | Medium |
| `session_for` | `reference/deck.mdx:562-565` | VALID | yes | the only mention of this public method on the site | High |
| context boundaries | `reference/deck.mdx:573-594` | VALID | yes | all three boundaries verified against `gateway.py`, `runners/agent.py`, `skills/` | High |
| workflow-parked limit | `reference/deck.mdx:596-603` | VALID | yes | matches `native/executor.py:105-108`; **contradicts `README.md:166` and `llms.txt:16`** | High |
| `run.can` gate | `reference/run.mdx:36-39` | VALID | yes | - | High |
| method list | `reference/run.mdx:16-29` | **INCOMPLETE** | yes | omits `run.id`/`key`/`namespace`/`session_id`, omits `events()`'s parameters and its suspension boundary, calls `await run` "the final output" (it is a `TurnResult` or the body's value), omits `RunSuspendedError` and `RuntimeError` | High |
| `KNOWN_KINDS` import | `reference/events.mdx:117-119` | VALID BUT MISLEADING | yes | the only import path that exists, and the one `write-your-own.mdx:59` forbids | High |
| `run.events` loop | `reference/events.mdx:109-112` | VALID | yes | - | High |
| envelope + 21 kind tables | `reference/events.mdx:21-105` | VALID | yes | every payload field checked against `core/events.py` | High |
| exports list | `reference/python-api.mdx` | **INCOMPLETE** | yes | covers `agentdeck/__init__.py` and `agentdeck.bindings` only. Absent: `agentdeck.errors`, `agentdeck.observers`, `agentdeck.testing`, the `views` members, `Skills`, `MCP`, `AgentDeclaration`, `NativeDefinition`, `Controls`, `InterruptResult`, `deck.session_for`, `deck.settings` | High |
| settings tables | `reference/settings.mdx` | VALID | yes | generated and pinned by `test_settings_reference_page_matches_the_generator` | High |
| CLI help | `reference/cli.mdx` | VALID | yes | generated and pinned. Its help text names a page, "Run Control", that does not exist | High |

## 7. Docs site: Resources and Jack

| Example | Location | Status | Current API? | Problem | Confidence |
|---|---|---|---|---|---|
| error taxonomy tables | `resources/troubleshooting.mdx:24-41` | VALID BUT MISLEADING | yes | "every one is an `AgentdeckError`, so one `except` catches the lot" is false for `await run`, which raises a bare `RuntimeError` for cancelled and rehydrated-failed runs (**run**). `UnsupportedControlError`'s stated cause is unreachable | High |
| `deck.serve(port=)` | `troubleshooting.mdx:47-49` | VALID | yes | - | High |
| unserializable tool return | `resources/known-issues.mdx:42-51` | VALID | yes | a defect report, accurate | Medium |
| v6.0.0 framing | `known-issues.mdx:17,68-79` | STALE | no | pinned to v6.0.0; 6.0.1 to 6.0.3 fixed `#487`, `#470` and `#471`, all in this page's own silent-failure class | High |
| `StoreError` on a 4.x log | `resources/migration-guides.mdx:49-58` | UNVERIFIED | yes | correctly marked `illustrative reason="requires a project pointed at a 4.x SQLite event log"` | High |
| `Observer` rename | `migration-guides.mdx:103-108` | VALID | yes | executed by the suite | High |
| Jack tools and agent | `jack.mdx:31-51` | VALID | yes | matches `examples/jack/jack/agent.py` | High |
| Jack SSE route | `jack.mdx:102-106` | VALID | yes | matches `jack/server.py:239-251`, and is the honest shape the landing page replaces with `deck.serve` | High |

## 8. Repository markdown

| Example | Location | Status | Current API? | Problem | Confidence |
|---|---|---|---|---|---|
| flagship agent + workflow + deck | `README.md:45-74` | **BROKEN** | yes | two independent failures, both reproduced (**run**). `ctx.invoke(agent, ...)` raises `ConfigError` because `_invoked_name` (`deck.py:277-313`) accepts a name, a `NativeDefinition` or an `AgentInstance`, not an `Agent`. With that fixed, `return response` raises `pydantic.ValidationError` because a `TurnResult` cannot be a `DataBlock`. `ctx.invoke("SupportBot", ...)` plus `return response.output` runs clean | High |
| `deck.stream` | `README.md:118-122` | VALID | yes | - | High |
| steer a run | `README.md:128-138` | **BROKEN** | yes | the wedging pause/resume pair, then `await run.answer(True)` on a run whose workflow has not reached its `ctx.ask` yet (an agent turn runs first), which raises | High |
| multi-binding serve | `README.md:104-110` | VALID | yes | - | High |
| `.agentdeck/` tree | `README.md:148-158` | VALID BUT MISLEADING | yes | tree names `workflows/handle_ticket/`; the code above defines `handle_request`, and the snippet runs `"handle_ticket"` | High |
| "approvals that outlive the process" | `README.md:166` | **BROKEN** claim | no | a suspended `@workflow` body dies with its process; a resume elsewhere raises `ConfigError` (`native/executor.py:84,105-108`) | High |
| "resumed by an asynchronous worker" | `README.md:140` | VALID BUT MISLEADING | yes | true for a paused agent turn with durable stores; false for a parked workflow, and neither precondition is stated | High |
| install + extras | `README.md:183-196` | VALID | yes | - | High |
| package tree | `agentdeck/README.md:14-24` | VALID | yes | matches the tree; omits `observers.py`, `views.py`, `testing.py`, `errors.py` | High |
| "`get_settings()` is not for application code" | `agentdeck/README.md` | VALID | yes | and violated by `examples/jack/jack/server.py:41` | High |
| `agentdeck/surfaces/` layer | `CLAUDE.md` §2 | STALE | no | no such package; it is `agentdeck/bindings/` and `agentdeck/adapters/bindings/` | High |
| "approvals that outlive the process" | `docs-site/public/llms.txt:16` | **BROKEN** claim | no | same as `README.md:166`; generated from a hand-written preamble | High |

## 9. `examples/`

| Example | Location | Status | Current API? | Problem | Confidence |
|---|---|---|---|---|---|
| chat agent with a tool | `examples/chat-agent-with-a-tool/` | VALID | yes | deck builds under `test_every_example_deck_builds`; `run.py` is 6 lines and correct. Its README's output is not asserted | High |
| agent with a skill | `examples/agent-with-a-skill/` | VALID | yes | builds, and `test_the_skill_example_declares_an_agent_holding_its_skill` checks the wiring. Needs a key, so the two-turn output is unasserted | High |
| chat in the terminal | `examples/chat-in-the-terminal/` | VALID | yes | builds; `test_the_terminal_example_declares_a_workflow_and_needs_no_model` covers it | High |
| "close the terminal and the run is still waiting" | `chat-in-the-terminal/README.md:41-43` | **BROKEN** claim | no | with the default `memory://` store the log dies with the process; with a durable store the parked body still dies, so answering raises `ConfigError`. `build-your-deck/workflows.mdx:72-74` states the correct version | High |
| "reachable over HTTP at the same time" | `chat-in-the-terminal/README.md:48-50` | VALID BUT MISLEADING | yes | true only if you add `Native.http()` yourself; `agentdeck chat` serves `Terminal.stdio()` alone (`cli.py:60`) | Medium |
| run events stream | `examples/run-events-stream/` | VALID | yes | **the best-verified example in the repo**: `test_the_events_example_prints_the_lifecycle_in_order` runs it and asserts the README's whole output block | High |
| "the same eight fields" | `run-events-stream/README.md` | VALID | yes | 8 envelope fields excluding `payload`, matching `reference/events.mdx` | Medium |
| Jack agent and tools | `examples/jack/jack/agent.py` | VALID | yes | - | High |
| Jack server | `examples/jack/jack/server.py` | VALID | yes | correct, and its docstring is the clearest statement anywhere of why a context cannot be served. Uses two internal import paths (`agentdeck.runtime.settings`, `agentdeck.core.ports`) the package README and `write-your-own.mdx` discourage | High |
| Jack eval harness | `examples/jack/eval.py`, `evals/**` | UNVERIFIED | yes | needs a live model | High |

## 10. Tests that function as examples

| Example | Location | Status | Notes |
|---|---|---|---|
| `tests/bindings/fixture_plugin/` | fixture plugin + its own `.importlinter` | VALID | the reference implementation `write-your-own.mdx` points at; held to the plugin import boundary the same way an out-of-tree package would be |
| `tests/bindings/test_contract.py` | SPI contract suite | VALID | the "Prove it" checklist on `write-your-own.mdx` is generalized from this |
| `tests/plays.py`, `tests/project_executors.py` | executor test seams | VALID | not user-facing; documents the private `_executors=` seam |
| `agentdeck/testing.py` | `ScriptedModel`, `patch_model`, `scripted_model_server` | VALID | public and useful; mentioned on exactly one docs page (`examples/run-events-stream.mdx`) and absent from `reference/python-api.mdx` |

---

## 11. Coverage summary

Measured across `docs-site/content/**/*.mdx` at this tag:

| Fence meta | Count | What is verified |
|---|---|---|
| ```` ```python run ```` | 8 | executed as a subprocess against a scripted model server |
| ```` ```python no-test reason= ```` | 10 | nothing, deliberately, with a stated reason |
| ```` ```python illustrative reason= ```` | 2 | nothing, deliberately, with a stated reason |
| ```` ```python file= ```` | 1 | written into the temp project a `run` fence uses |
| ```` ```python ```` (bare) | 62 | AST parses; `from agentdeck...` names resolve |
| **total** | **83** | |

Executed fences, by page: `sessions.mdx` 3, `reference/deck.mdx` 2, `tools.mdx` 1,
`context.mdx` 1, `migration-guides.mdx` 1.

Pages with zero executed fences that contain runnable code: `quickstart.mdx`, `runs.mdx`,
`events.mdx`, `lifecycle-and-control.mdx`, `pause-resume.mdx`, `human-input.mdx`,
`workflows.mdx`, `agents.mdx`, `deck.mdx`, every `bindings/*` page, `reference/run.mdx`,
`reference/events.mdx`.

`tests/test_docs_examples.py` and `tests/test_docs_site.py` both pass at this tag
(285 + 10 tests). Every BROKEN row above sits in the bare-fence 62 or in repository markdown,
neither of which the suite executes.

## 12. Status tally

131 rows, across sections 1 to 10.

| Status | Docs site | Repo markdown | `examples/` | Tests | Total |
|---|---|---|---|---|---|
| VALID | 72 | 5 | 7 | 4 | 88 |
| VALID BUT MISLEADING | 13 | 2 | 1 | 0 | 16 |
| INCOMPLETE | 4 | 0 | 0 | 0 | 4 |
| STALE | 3 | 1 | 0 | 0 | 4 |
| BROKEN (incl. broken claims) | 8 | 4 | 1 | 0 | 13 |
| UNVERIFIED | 5 | 0 | 1 | 0 | 6 |
| **total** | **105** | **12** | **10** | **4** | **131** |

The 13 BROKEN rows reduce to seven distinct defects, since four are copies of the same
pause/resume idiom and two are the same durability claim:

1. `pause()` immediately followed by `resume()` (4 copies: quickstart, `pause-resume`,
   `lifecycle-and-control`, README).
2. `ctx.invoke(agent, ...)` with an `Agent` object (README).
3. `return response` where `response` is a `TurnResult` (README).
4. `run.answer({"approved": True})` against an options-bearing ask (`human-input`).
5. `# ValueError` where `InputError` is raised (`workflows`).
6. Serving a `context=` deck (landing page).
7. "Approvals / runs outlive the process" (README twice, `llms.txt`, `chat-in-the-terminal`
   README).

Plus one reference statement that is false as written: `deck.workflows` mutation
(`reference/deck.mdx:100`).
