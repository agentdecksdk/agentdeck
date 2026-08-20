# SDK drift audit

Repo: agentdeck-audit, branch audit/sdk-report. Scope: README, docs/engineering, examples/, CHANGELOG, docs-site. All checks are static reads, no code executed.

## 1. README.md claims

| Claim | Verified | Evidence |
|---|---|---|
| `pip install agentdeck-sdk` / dist name `agentdeck-sdk`, import `agentdeck` | yes | `pyproject.toml:2` |
| `from agentdeck import Agent, Deck` | yes | `agentdeck/__init__.py:12,39-53` |
| `from agentdeck import Agent, Context, Deck` | yes | `agentdeck/__init__.py:12-13,39-53` |
| `Agent(name=..., instructions=..., tools=[...])` | yes | `agentdeck/authoring/agent.py:91-112` |
| `Deck(agents=[jack])`, `Deck(agents=[jack], context=DocsCorpus)` | yes | `agentdeck/deck.py:390-397` (`context: object = None` param) |
| Tool `Context[DocsCorpus]` param pattern | yes | `agentdeck/authoring/agent.py:60-61` (dynamic-instructions/context injection docstring) |
| `async with deck:` context manager | yes | `agentdeck/deck.py:607` `__aenter__`, `:700` `__aexit__` |
| `deck.stream("Jack", question, context=corpus)` | yes | `agentdeck/deck.py:911-916` |
| event kinds `run.started`, `tool.call.started`, `text.delta`, `run.completed` | yes | `agentdeck/core/events.py:109,122,220,244` |
| `deck.runs.start(...)` | yes | `agentdeck/deck.py:1306` class `Runs`, `:1322` `start` |
| `run.pause()/resume()/cancel()/answer(...)` | yes | `agentdeck/deck.py:1184,1191,1196,1211` |
| `deck.runs.get(id)` | yes | `agentdeck/deck.py:1358` |
| `Deck.from_project()` discovers `./.agentdeck` | yes | `agentdeck/deck.py:449-450`; `PROJECT_DIR = ".agentdeck"` in `agentdeck/runtime/registry.py:18` |
| `.agentdeck/agents/<name>/agent.py`, `workflows/<name>/workflow.py`, `skills/<name>/SKILL.md` layout | yes | `agentdeck/deck.py:459-466` (`type_dir="agents"`/`"workflows"`); `agentdeck/skills/bundle.py:14` `SKILL_MD_FILENAME = "SKILL.md"` |
| `deck.run("Greeter", "hello")` | yes | `agentdeck/deck.py:869` `async def run` |
| examples list: chat agent w/ tool, workflow pausing for approval, wrapped LangGraph agent, Jack | yes | `examples/chat-agent-with-a-tool/`, `examples/workflow-with-an-approval/`, `examples/existing-langgraph-agent/`, `examples/jack/` all present |
| Extras: `serve`, `durability`, `redis`, `observability`; SQLite ships in base | yes | `pyproject.toml:43-63` (`serve`, `observability`, `durability`, `redis` sections) |
| `OPENAI_MODEL`, `OPENAI_API_KEY`, `OPENAI_BASE_URL` env vars | yes | `agentdeck/runtime/settings.py:243-245` |
| `agentdeck/README.md` (framework internals) referenced | yes | file exists at `agentdeck/README.md` |
| `good first issue` label, scoped to an afternoon | yes | `gh label list`: label exists; scoping claim not independently checkable (policy, not code) |
| `docs/brand/`, `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `LICENSE` referenced | yes | all present at repo root / `docs/brand/` |

No unverified or false README claims found.

## 2. docs/engineering/*.md divergences

Spot-checked concrete-artifact claims only (paths, import exceptions, make targets). No divergences found.

| File | Concrete claim | Status |
|---|---|---|
| `import-boundaries.md:26` | `agentdeck/testing.py` imports narrow provider/test model types | matches: `agentdeck/testing.py:24-36` imports `agents.items`, `agents.models.interface`, `agents.usage`, `openai.types.responses.*` |
| `import-boundaries.md:27` | `agentdeck/authoring/` imports provider SDK types for compilation | matches: `agentdeck/authoring/compile.py:43-45`, `nodes.py:17`, `tools.py:26`, etc. import `agents`/`langgraph` |
| `import-boundaries.md:28` | `agentdeck/adapters/tools/mcp/` has MCP-provider import exception | matches: `agentdeck/adapters/tools/mcp/transport.py:17` `from agents.mcp import MCPServerStreamableHttp` |
| `coding-standards.md` links to 7 sibling docs | all resolve | `docs/engineering/{principles,architecture,import-boundaries,runtime-contracts,testing,dependencies,repository-policy,coding-agents}.md` all exist |
| Implicit `make check` gate (referenced via CLAUDE.md, not engineering docs directly) | matches | `Makefile:48` `check: lint typecheck lint-imports test` |
| Implicit `make golden` (referenced via CLAUDE.md) | matches | `Makefile:31` `golden:` re-record wire/schema snapshots |

## 3. examples/ import check (static only)

| Example | Top-level agentdeck imports | Resolves |
|---|---|---|
| `agent-with-a-skill` | `Agent`, `Deck` | yes |
| `chat-agent-with-a-tool` | `Agent`, `Deck` | yes |
| `existing-langgraph-agent` | `Workflow`, `Deck` | yes |
| `workflow-with-an-approval` | `Workflow`, `Deck`, `agentdeck.core.RunStatus` | yes (`agentdeck/core/__init__.py:57,94`) |
| `jack` | `Deck`, `Agent`, `Context`, `agentdeck.runtime.settings.get_settings` | yes (`agentdeck/runtime/settings.py:605`) |

No stale examples found; all imported symbols exist in current `agentdeck/` exports.

## 4. CHANGELOG spot-check

`## [Unreleased]` is empty. Checked last 2 released versions (4.0.5, 4.0.4), 5 random claims:

| Claim | Location | Verified | Evidence |
|---|---|---|---|
| Jack citation slugs render as links | `CHANGELOG.md:15-18` (4.0.5) | yes | `docs-site/app/jack-citations.ts` added in commit `0f55842`/PR #358 |
| `jack.session.BoundedSessions` is a `Deck(session_factory=...)` | `CHANGELOG.md:27-29` (4.0.4) | yes | `examples/jack/jack/session.py:87` class exists; `agentdeck/deck.py:399` `session_factory` param exists |
| `AGENTDECK_LANGFUSE_PUBLIC_KEY` attaches Langfuse observer | `CHANGELOG.md:30-33` (4.0.4) | yes | `agentdeck/observers.py:91`, `examples/jack/README.md:21` |
| Real `RunStatus` values are `running/paused/waiting_answer/completed/failed/cancelled` | `CHANGELOG.md:38-39` (4.0.4, Fixed) | yes | `agentdeck/core/status.py:40-45` exact match |
| 21 event kinds exist (vs. stale docs' claim of different set) | `CHANGELOG.md:40-41` (4.0.4, Fixed) | yes | `grep -c 'kind: Literal\[' agentdeck/core/events.py` = 21 |

## 5. docs-site structure and duplication with docs/

| Tree | Top-level dirs |
|---|---|
| `docs/` (internal) | `brand/`, `delivery/` (25 files), `design/` (8), `engineering/` (10), `prompts/` (3), plus 5 root md files |
| `docs-site/content/` (public Nextra site) | `build-your-deck/` (7), `examples/` (2), `integrations/` (5), `jack/` (2), `meet-agentdeck/` (4), `reference/` (7), `resources/` (5), plus 3 root files |

No topical duplication in prose content: `docs/` is internal engineering/PRD/delivery tracking, `docs-site/content/` is the public-facing SDK documentation.

One asset duplication: `docs/brand/` and `docs-site/public/brand/` both carry identical-named SVGs (`logo*.svg`, `favicon.svg`, `social-card.svg`, `contributor-*.svg`) - source assets copied into the Next.js `public/` dir for serving. `docs-site/public/brand/` additionally has `card.svg`, `spark.svg`, `wordmark.svg` not present in `docs/brand/`.
