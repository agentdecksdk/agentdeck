# AgentDeck SDK: Verdict

One audit, 376 evidence-backed findings (157 good, 219 bad) across eleven reports: seven on the code and its surfaces, four on the cross-cutting layers (coding craft, dev process, mental model, branding/adoption). Every claim below links to the file that holds the snippet, the `file:line`, and the depth. Headline claims were re-verified against source by the audit lead.

**Executive summary.** This is a genuinely well-engineered SDK with a clear philosophy that it mostly honors: pure core, real ports, disciplined stores, and a test suite better than most commercial products ship. Its failures cluster at four seams: the typing story stops where the runtime meets the user, the distributed story stops where the event log ends, the docs story stops where the generator stops, and the process story stops where automation ends: every excellent gate is advisory because nothing on GitHub enforces it, and everything that ships runs through one person and in part one laptop. None of the worst findings are rot; they are unfinished edges of a design that is otherwise coherent.

## The best

Things I rarely see done this well, in any codebase:

- **The lifecycle is a total table that fails at import.** No invalid transition is expressible because no branch decides one. Crash recovery is proven with real SIGKILL across OS processes, not mocks. ([03-runtime.md](03-runtime.md))
- **Zero mocks in 21K test LOC.** One contract suite runs 56 cases against all four real store backends, races are arranged through files across real processes, and CI greps skip reasons so a subsystem cannot drop out silently. ([05-testing.md](05-testing.md))
- **`Context[T]` injection.** Plain undecorated functions as tools, one portable context type above both engines, invisible to the model's schema. The single best idea in the API. ([02-api-design.md](02-api-design.md))
- **Store adapters with correct transaction posture.** SQLite `BEGIN IMMEDIATE` before the read, Postgres advisory locks with pinned isolation, Redis key escaping with the exact failure mode documented. ([04-adapters.md](04-adapters.md))
- **Docs that cannot drift, where generated.** Settings and CLI reference pages are byte-compared in the gate; doc fences can execute as real subprocesses; examples are built by the test suite. README, engineering docs, examples, and CHANGELOG verified clean of drift. ([06-docs-dx.md](06-docs-dx.md))
- **A CI gate that documents its own scar tissue.** Every non-obvious step cites the incident that made it necessary, the release gate is the merge gate re-run, PyPI uses trusted publishing, and the skip-hunter refuses a green run that measured nothing. Median CI: 2 minutes. ([09-dev-process.md](09-dev-process.md))
- **Deliberate debt with named ceilings.** All 12 `ponytail:` markers state the shortcut, its ceiling, and the trigger to upgrade; every lint/type suppression carries its reason inline; policy lives in tables, not `if` chains. ([08-coding-patterns.md](08-coding-patterns.md))
- **Adoption built on measurement, not vibes.** A 30-query discoverability baseline that honestly reports zeros, a byte-pinned `llms.txt`, a hardened `pull_request_target` contributor bot, and Jack: a live agent built with the product, answering for the product, on the product's homepage. ([11-branding-adoption.md](11-branding-adoption.md))

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
9. **Zero enforcement behind excellent gates.** No branch protection on any branch, 145 of 164 merged PRs with no recorded review, one PR merged 2m41s before its own CI finished, and the review gate lives in a terminal where nothing on GitHub records it ran. Bus factor is structurally one: every merge, release, docs redeploy, and the production Jack process go through one person, and Jack runs as a hand-started uvicorn behind a hand-started tunnel on a laptop. No staging exists anywhere. ([09-dev-process.md](09-dev-process.md))
10. **The differentiation is measured, prioritized, and unwritten.** The baseline named "use your existing LangGraph agent" as the one winnable query; that page is 7 lines. `context7.json` tells LLMs "there is no `agentdeck-sdk` package" one line above the `pip install agentdeck-sdk` line, the repo description is visibly mangled, the social card is declared but no image supplied, and nothing anywhere answers "why not just LangGraph". ([11-branding-adoption.md](11-branding-adoption.md))
11. **Three identity vocabularies for one system.** LangGraph's `thread_id` and fused resume-means-answer leak in through the HTTP surface, the Agents SDK's `Runner`/`Session`/turn leak in through settings and the main return type, and the page titled "Mental Model" is fifteen lines. The six states and four verbs are right; the words are not defended at the borders. ([10-mental-model.md](10-mental-model.md))

## The good

- Import boundaries machine-enforced, 11 contracts green; core is stdlib+pydantic only. ([01](01-architecture.md))
- Optional extras genuinely optional and lazily imported, each `ImportError` naming the extra and a docs link. ([01](01-architecture.md))
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
- An authoring-ring import cycle held together by nine function-local imports; a sync bridge that blocks the loop it detected; the `_UNSET` merge ladder hand-written twice; 260 `Any` annotations with no way to tell contract from shrug; the comment-rarity rule waived 1,014 times (the rule is wrong, not the code). ([08](08-coding-patterns.md))
- CONTRIBUTING's setup line installs fewer extras than the gate needs (the exact trap CI was built to catch); "CI runs `make check`" is false in both directions; only Python 3.13 is ever tested against a 3.12 floor; no `timeout-minutes` anywhere and one run burned six hours; 167 historical commits carry the forbidden AI trailers, one leaking a session URL. ([09](09-dev-process.md))
- Four "good first issues" (#332-#335) are the same control/lease gap report 04 rates high severity; the greeting bot was dead for four days with every run green and has never auto-fired for a real outside contributor; the public roadmap shipped and was silently deleted; "production runtime" is claimed on five surfaces against a Beta classifier and four majors in 24 days. ([11](11-branding-adoption.md), [09](09-dev-process.md))
- The product noun differs across eight surfaces (harness, runtime, production runtime, declarative harness...); the deck-of-cards metaphor exists only in pixels, never in the prose. ([10](10-mental-model.md), [11](11-branding-adoption.md))

## If I could only fix eight things

1. Quickstart + the 15 stub pages (delete or write them; stubs that lie are worse than 404s), and write the one measured-winnable page: "use your existing LangGraph agent".
2. Typed results: rehydrate `output_type`/`state` models; move the two `RuntimeError`s into the taxonomy.
3. Escape the session key (one line) and add postgres/redis control+lease backends, or document the single-node ceiling loudly.
4. Rename or gut `Agent.run()`/`Workflow.run()`.
5. Turn the MCP resilience path's tests on and re-enable the schema compat guardrail.
6. Turn on branch protection with required checks and a required review on `dev` and `main`: an afternoon that converts every advisory gate into a real one.
7. Move Jack and the docs redeploy off the laptop; version `redeploy.sh` or delete the runbook step that names it.
8. Fix the three broken adoption artifacts an LLM or evaluator hits first: `context7.json` rule 24, the mangled repo description, the missing og:image.
