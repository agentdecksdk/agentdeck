# AgentDeck SDK: Verdict

One audit, 221 evidence-backed findings (95 good, 126 bad) across seven reports. Every claim below links to the file that holds the snippet, the `file:line`, and the depth. Headline claims were re-verified against source by the audit lead.

**Executive summary.** This is a genuinely well-engineered SDK with a clear philosophy that it mostly honors: pure core, real ports, disciplined stores, and a test suite better than most commercial products ship. Its failures cluster at three seams: the typing story stops where the runtime meets the user, the distributed story stops where the event log ends, and the docs story stops where the generator stops. None of the worst findings are rot; they are unfinished edges of a design that is otherwise coherent.

## The best

Things I rarely see done this well, in any codebase:

- **The lifecycle is a total table that fails at import.** No invalid transition is expressible because no branch decides one. Crash recovery is proven with real SIGKILL across OS processes, not mocks. ([03-runtime.md](03-runtime.md))
- **Zero mocks in 21K test LOC.** One contract suite runs 56 cases against all four real store backends, races are arranged through files across real processes, and CI greps skip reasons so a subsystem cannot drop out silently. ([05-testing.md](05-testing.md))
- **`Context[T]` injection.** Plain undecorated functions as tools, one portable context type above both engines, invisible to the model's schema. The single best idea in the API. ([02-api-design.md](02-api-design.md))
- **Store adapters with correct transaction posture.** SQLite `BEGIN IMMEDIATE` before the read, Postgres advisory locks with pinned isolation, Redis key escaping with the exact failure mode documented. ([04-adapters.md](04-adapters.md))
- **Docs that cannot drift, where generated.** Settings and CLI reference pages are byte-compared in the gate; doc fences can execute as real subprocesses; examples are built by the test suite. README, engineering docs, examples, and CHANGELOG verified clean of drift. ([06-docs-dx.md](06-docs-dx.md), `_scout/drift.md`)

## The worst

Things that bite a real user, ranked by who bleeds first:

1. **The Quickstart cannot produce the output it shows.** `main()` is never called, and neither required env var is mentioned. The one page every new user reads first fails on first contact, and 15 of 33 docs-site pages are stubs, some stating APIs that do not exist (a `WAITING` status, `AGENTDECK_SERVE_PORT`), one already leaked into `llms-full.txt`. ([06-docs-dx.md](06-docs-dx.md))
2. **You declare a pydantic model and get a dict back**, and `await run` raises bare `RuntimeError` outside the advertised `except AgentdeckError` contract. The two sharp edges on the primary path, hit on day one. ([02-api-design.md](02-api-design.md))
3. **The distributed story is half-shipped.** Event stores reach postgres/redis; control signals and leases stop at memory/sqlite. The exact deployment the Postgres store exists for has no cross-node cancel and no cross-node liveness, and the default config reports a cancel as delivered that another process can never see. ([03-runtime.md](03-runtime.md), [04-adapters.md](04-adapters.md))
4. **Cross-namespace session bleed.** The openai-agents session key concatenates namespace and log key with a bare colon; the same repo escapes exactly this in its Redis store. One line, tenant-isolation bug. ([04-adapters.md](04-adapters.md))
5. **`Agent.run()` / `Workflow.run()` are traps wearing the main verb.** They silently skip the event log, observers, cancellation, and persistence, and speak a different vocabulary. A second execution path the architecture pays for everywhere. ([01-architecture.md](01-architecture.md), [02-api-design.md](02-api-design.md))
6. **SECURITY.md promises isolation no mechanism provides.** No caller identity anywhere: any caller resumes any conversation by naming it, the approval endpoint answers to anyone, and the default bind is `0.0.0.0`. ([07-security-deps.md](07-security-deps.md))
7. **LangGraph runs report zero token usage, always.** Every workflow is cost-invisible to every dashboard. ([04-adapters.md](04-adapters.md))
8. **The two riskiest code paths are untested.** The MCP transport's 234-line self-healing path runs in no test, and the schema forward-compat guardrail has been skipped since two major versions before its own stated re-enable condition was met. ([05-testing.md](05-testing.md))

## The good

- Import boundaries machine-enforced, 11 contracts green; core is stdlib+pydantic only. ([01](01-architecture.md))
- Optional extras genuinely optional and lazily imported, each `ImportError` naming the extra and a docs link. ([01](01-architecture.md), `_scout/deps.md`)
- Error messages that name the object, the cause, the fix, and the tracking issue. ([02](02-api-design.md), [06](06-docs-dx.md))
- Store assigns `seq`/`ts` in the persisting write; every transition is a conditional append; sink fan-out is bounded and never blocks a run. ([03](03-runtime.md))
- Zero dynamic execution primitives, fully parameterized SQL, secret-aware error text, honest SECURITY.md about tool trust. ([07](07-security-deps.md))
- Five progressive examples whose READMEs document the footguns, with a deployed reference app (Jack). ([06](06-docs-dx.md))

## The bad

- `deck.py` is a 1413-line god module; 19% of the SDK lives outside the five declared rings; the import contracts are deny-lists, so new modules default to unconstrained. ([01](01-architecture.md))
- `ToolSourcePort` has zero production consumers while a process-global `MCPLifecycle` singleton does the real work; one Deck per process is a module global. ([01](01-architecture.md))
- Required params hidden behind `_UNSET: Any`, `**kwargs` front doors, `-> Any` on known types, errors raised outside the taxonomy, `SkillError` exported but never raised. ([02](02-api-design.md))
- Non-atomic terminate sequence; `claim_resume` contract understates its condition; every turn reads the session's whole history; `list_runs` scans the whole namespace. ([03](03-runtime.md))
- MCP recovery replays `call_tool` (at-least-once for non-idempotent tools), hangs off a private SDK method, sleeps after the final retry; Redis client has no timeouts and breaks on Cluster; `aiosqlite` used but undeclared; `opentelemetry-sdk` declared but unused. ([04](04-adapters.md), [07](07-security-deps.md))
- `test_deck.py` at 2130 lines; goldens rewritable by an exported env var; no `filterwarnings=error` over two fast-moving SDK deps. ([05](05-testing.md))
- One-command CLI with no description and no `runs list`; no scaffolder for the `.agentdeck/` layout the product is built around; four majors in three weeks against an 8-line migration page. ([06](06-docs-dx.md))
- Dockerfile runs as root on a floating tag ignoring the lockfile; `.mcp.json` and `config.yaml` hold secrets and are not gitignored; a typo'd store URL echoes the password into the error. ([07](07-security-deps.md))

## If I could only fix five things

1. Quickstart + the 15 stub pages (delete or write them; stubs that lie are worse than 404s).
2. Typed results: rehydrate `output_type`/`state` models; move the two `RuntimeError`s into the taxonomy.
3. Escape the session key (one line) and add postgres/redis control+lease backends, or document the single-node ceiling loudly.
4. Rename or gut `Agent.run()`/`Workflow.run()`.
5. Turn the MCP resilience path's tests on and re-enable the schema compat guardrail.
