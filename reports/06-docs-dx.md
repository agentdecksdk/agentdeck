# Docs and developer experience

AgentDeck's onboarding is two projects wearing one coat. The README, the five runnable examples, the generated reference pages, the error taxonomy and the CHANGELOG are the best work in the repo and beat both competitive baselines outright; the hand-written half of the docs site is 15 stub pages that describe an API the code does not have, and the primary Quickstart does not run as written.

`reports/_scout/drift.md` found zero drift across README claims, `docs/engineering/`, example imports and CHANGELOG spot-checks. I re-verified four of its rows independently (`pyproject.toml:2` dist name, `registry.py:18` `PROJECT_DIR`, the 21 `kind: Literal[` declarations in `core/events.py`, and the six `RunStatus` members at `core/status.py:40-45`) and all four hold. Credit to that scout: the claims a reader can check against code are clean. Everything below is what a drift check by construction cannot see, namely pages that are internally consistent because they say almost nothing.

---

### Examples are minimal, runnable and genuinely progressive [GOOD] (severity: high)
Five examples, each one new concept, each `run.py` under 20 lines with no scaffolding. The ladder is real: tool, then skill plus a session, then a durable interrupt, then a foreign LangGraph graph, then Jack at 12 modules with evals.
```python
import asyncio
from agentdeck import Deck

async def main() -> None:
    async with Deck.from_project() as deck:
        result = await deck.run("OrderDesk", "where is order A-1001?")
        print(result.output)

asyncio.run(main())
```
Evidence: `examples/chat-agent-with-a-tool/run.py:1`

### Time-to-first-agent is four concepts and two env vars [GOOD] (severity: high)
From `pip install agentdeck-sdk` to a running agent the reader must learn `Agent`, `Deck`, two env vars and `asyncio`. Nothing else: no graph, no state schema, no node registration, no checkpointer decision, no session or event concept until they want one. That concept surface is smaller than any comparable harness in this space, and it is the pitch working.
```python
from agentdeck import Agent, Deck

jack = Agent(
    name="Jack",
    instructions="Help developers build with AgentDeck.",
)

deck = Deck(agents=[jack])
```
Evidence: `README.md:37`

### Example READMEs document the footguns, not the happy path [GOOD] (severity: high)
Each README has a "What to look at" section naming what will bite you. The approval example explains that an interrupting node re-runs from its start on resume, which is the single most expensive thing to learn from production.
```python
- **The interrupting node re-runs from its start on resume.** `_confirm` therefore does nothing
  but ask, `_price` does its work before the pause, and `_settle`  -  the node that would actually
  move money  -  runs after the decision. A side effect inside `_confirm` would happen twice.
```
Evidence: `examples/workflow-with-an-approval/README.md:39`

### Every example's Deck is built by the test suite [GOOD] (severity: medium)
The README claims "All are built by the test suite, so none can quietly stop working" and that claim is true, including a test that the file `python run.py` names actually exists.
```python
def test_every_example_deck_builds(example: Path) -> None:
def test_every_example_has_the_run_script_its_readme_tells_you_to_run(example: Path) -> None:
```
Evidence: `tests/test_examples.py:44`

### Doc snippets can be assembled into a project and executed for real [GOOD] (severity: high)
A `file=` fence writes into a temp project and a `run` fence executes it as a subprocess against a scripted OpenAI-compatible server, with nothing in `agentdeck` patched. This is well above the bar either competitor sets for prose docs.
```python
- ``file=<relative-path>``  -  write this fence's source verbatim into the page's shared temp
  project, at ``<relative-path>``. Not executed by itself.
- ``run``  -  execute this fence as a real subprocess against that same temp project, once
  per fence.
```
Evidence: `tests/test_docs_examples.py:5`
Ref: https://github.com/openai/openai-agents-python

### The settings and CLI reference pages are generated and byte-compared in the gate [GOOD] (severity: high)
`reference/settings.mdx` is rendered from the `LayeredSettings` subclasses and asserted byte-equal in `make check`, so every `AGENTDECK_*` variable, type, default and description is structurally incapable of drifting. This is the strongest single docs asset in the repo.
```python
def test_settings_reference_page_matches_the_generator() -> None:
    assert SETTINGS_PAGE.read_text() == render_settings_mdx(), ...
```
Evidence: `tests/test_generated_reference.py:43`

### Error messages hand back the exact code to write [GOOD] (severity: high)
Of 10 raise sites sampled across `runtime/`, `skills/`, `deck.py` and `adapters/engines/`, 7 fully meet the house rule. The best ones name the line to add or the issue number that explains the refusal.
```python
raise ConfigError(
    f"{bundle_file} imported cleanly but defines no {self.label}  -  a declaration "
    f"subclass alone contributes nothing; add `{var} = {self.base_class.__name__}(...)` "
    "at module level."
)
```
Evidence: `agentdeck/runtime/registry.py:118`

### The error taxonomy documents state machines, not just exception names [GOOD] (severity: medium)
Each class docstring explains what the refusal means, why it is not a race, and what the caller's recovery path is. `DuplicateKeyError` naming `get(namespace=, key=)` instead of "retry" is the kind of detail that saves a wrong fix.
```python
class DuplicateKeyError(AgentdeckError):
    """A run started with a ``key`` already claimed by another run in the same namespace.

    ``(namespace, key)`` is consumed permanently once a run opens with it  -  not merely while
    that run is active  -  so this is not a race retried away; ...
    The caller's recovery path is ``get(namespace=, key=)``, not a retry.
    """
```
Evidence: `agentdeck/errors.py:81`

### Retired env vars refuse to boot with the exact replacement named [GOOD] (severity: medium)
Nine v2-era variable names are mapped to their replacements and a process exporting a dead one with nothing set in its place will not start. This is the only upgrade aid in the project that actually works, and it is better than a migration page would be.
```python
_RETIRED_ENV_NAMES: Mapping[str, str] = {
    "AGENTDECK_EVENTS_BACKEND": "AGENTDECK_EVENTS",
    "AGENTDECK_CONTROL_BACKEND": "AGENTDECK_CONTROL",
    "AGENTDECK_SESSION_REDIS_URL": "AGENTDECK_SESSION",
    "APP_CONFIG_PATH": "AGENTDECK_CONFIG_PATH",
}
```
Evidence: `agentdeck/runtime/settings.py:568`

### The CHANGELOG is written for a reader, and admits its own documentation bugs [GOOD] (severity: medium)
Entries lead with a bolded claim, then the user impact, then the cause. The 4.0.4 "Fixed" entry publicly lists three doc pages that stated things the code does not do, naming the invented values. That is rarer than it should be.
```python
- **Three documentation pages stated things the code does not do.** `runs-and-control/lifecycle-and-control`
  listed a `QUEUED` state and uppercase names; the six real values are `running`, `paused`,
  `waiting_answer`, `completed`, `failed` and `cancelled`. `reference/events` listed
  `message.delta`, `tool.call` and `tool.result`, none of which exist, and omitted 14 kinds ...
```
Evidence: `CHANGELOG.md:37`

### A published Known Issues page with reproduced silent-failure defects [GOOD] (severity: medium)
118 lines of real open defects, ranked by how quietly they fail, including one where a closed issue's fix shipped only half. Almost nobody publishes this.
```python
Everything here is real, reproduced, and open against **v4.0.0**. It is published rather than
quietly tracked because most of these fail *silently*  -  you get a plausible wrong answer, not an
error  -  and an hour spent debugging one of them is an hour this page could have saved.
```
Evidence: `docs-site/content/resources/known-issues.mdx:8`

### Jack is a reference application that is really deployed [GOOD] (severity: medium)
The README's Jack walkthrough is the actual source of the assistant serving the docs site, verified: `search_docs`, `read_doc` and the `Context[DocsCorpus]` signature match `examples/jack/jack/agent.py` exactly. A live reference app is a stronger teaching artifact than any tutorial.
```python
def search_docs(query: str, docs: Context[DocsCorpus]) -> str:
    """Find AgentDeck documentation pages matching a query."""
    return docs.data.search(query)
```
Evidence: `examples/jack/jack/agent.py:15`

---

### 15 of the docs site's 33 pages are 7-to-13-line stubs [BAD] (severity: high)
Whole nav sections are placeholders: all four `integrations/` pages are 7 lines, and the six-page "Build Your Deck" section the README links as the main learning path totals 85 lines against `reference/deck.mdx`'s 548. A reader who follows the README's own recommended route lands on empty rooms.
```python
# Context

Context provides strongly-typed runtime dependencies and request-scoped state to agents and tools.

## Using Context

Pass application context during run initialization to safely inject dependencies.
```
Evidence: `docs-site/content/build-your-deck/context.mdx:1`

### The stub pages state an API the code contradicts [BAD] (severity: high)
`human-input.mdx` tells the reader a run enters `WAITING`; there is no such status (`running/paused/waiting_answer/completed/failed/cancelled`). `troubleshooting.mdx` tells them to configure `AGENTDECK_SERVE_PORT`, which appears nowhere in `agentdeck/`. The stubs are short enough to look harmless and are actively wrong.
```python
## Answering Prompts

When a run enters `WAITING`, supply input with `run.answer()`:
```
Evidence: `docs-site/content/runs-and-control/human-input.mdx:7`

### The invented setting propagates into the LLM-facing corpus [BAD] (severity: medium)
`AGENTDECK_SERVE_PORT` is carried verbatim into `docs-site/public/llms-full.txt`, so the fabricated variable is what any coding assistant reading the project's own machine-readable docs will suggest. Unlike `settings.mdx` and `cli.mdx`, `llms-full.txt` is not byte-compared: its test only asserts the render is non-empty and the file exists.
```python
def test_llms_full_txt_can_be_regenerated() -> None:
    assert render_llms_full_txt().strip(), "llms-full.txt rendered empty"
    assert LLMS_FULL_PAGE.exists(), f"{LLMS_FULL_PAGE} is missing  -  {_REGEN_HINT}"
```
Evidence: `tests/test_generated_reference.py:63`

### The Quickstart never tells the reader to set a model or an API key [BAD] (severity: high)
Step 01 is `pip install`, step 02 is Python. `OPENAI_MODEL` is a required field with no default, so the reader's first action after following the page is a pydantic `ValidationError`. Every example README gets this right; the site's primary funnel page omits it entirely.
```python
model: str = Field(description="Model name passed to the host Agents SDK runner. No default  -  always required.")
```
Evidence: `agentdeck/runtime/settings.py:220`

### The Quickstart's script defines `main()` and never calls it [BAD] (severity: high)
Step 03 is copy-pasteable and inert: no `asyncio.run(main())`. Step 04 then shows the transcript "running the script" emits. The page's own `<Contribute>` block at line 96 concedes it covers only the path where everything is configured correctly.
```python
async def main():
    async with deck:
        run = await deck.runs.start("assistant", input="Hello!")
        async for event in run.events(follow=True):
            print(event.kind)
        result = await run
```
Evidence: `docs-site/content/meet-agentdeck/quickstart.mdx:37`

### 32 of the site's 39 Python fences are silently unexecuted [BAD] (severity: high)
The execution machinery exists and only two fences use it, both on `reference/deck.mdx`. Four opt out honestly with `no-test reason=`; the remaining 32, including all three Quickstart fences, are parse-only by default. The `illustrative reason="..."` escape hatch the test docstring documents as the deliberate opt-out is used zero times, so "unexecuted" is the default rather than a decision.
```python
     32          (bare ```python)
      2  run
      4  no-test reason="..."
      1  file=.agentdeck/agents/greeter/agent.py
```
Evidence: `tests/test_docs_examples.py:122`

### The most likely first error is a bare `FileNotFoundError` with a path [BAD] (severity: high)
Running from the wrong directory is the number one newcomer mistake, which is why all four example READMEs warn about it in italics. The error says nothing about `cd`, nothing about the expected layout, and links no docs, in a codebase where `registry.py:118` hands back the literal line of code to add.
```python
resolved = Path(root).resolve()
if not resolved.is_dir():
    raise FileNotFoundError(f"project dir not found: {resolved}")
```
Evidence: `agentdeck/runtime/registry.py:151`

### Configuration errors fall through to raw pydantic [BAD] (severity: medium)
`errors.py` declares this deliberate, and for internal validators it is defensible. For `OPENAI_MODEL`, the first variable every user must set and the one that is not `AGENTDECK_*`-prefixed like everything else, the reader's very first failure arrives as a library traceback with no docs link and no `AgentdeckError` to catch.
```python
Not everything is migrated, deliberately: pydantic ``field_validator`` bodies
keep raising ``ValueError`` (pydantic only folds those into
``ValidationError``), and missing-path / workspace faults keep their stdlib
types (``FileNotFoundError``, ``RuntimeError``).
```
Evidence: `agentdeck/errors.py:6`

### Error messages route the reader to stub pages [BAD] (severity: medium)
Three of the four docs constants used in error text point at pages with no content: `/build-your-deck/skills` and `/runs-and-control/sessions` are 7 lines each, `/build-your-deck/workflows` is 13. The "exact fix" half of the house rule is delegated to a link, and the link lands on a sentence.
```python
_SKILLS_DOCS = f"{DOCS_URL}/build-your-deck/skills"
```
Evidence: `agentdeck/skills/__init__.py:21`

### The link-rot test guards every hostname except the one that rotted [BAD] (severity: medium)
`SITE_LINK` matches `agentdecksdk.com` and `agentdecksdk.github.io`. The docstring above it names the `sagi5060` to `agentdecksdk` owner rename as exactly the sweep that leaves a link behind, then omits `sagi5060.github.io` from the pattern. Two dead links survive in an example README, at paths (`concepts/skills`, `guides/add-a-tool`) that no longer exist in the site's information architecture, and `docs/delivery/plan-adoption.md:62` had already flagged the fix as owed.
```python
SITE_LINK = re.compile(r"https://(?:agentdecksdk\.com|agentdecksdk\.github\.io/agentdeck)/([\w/-]*)")
```
Evidence: `tests/test_docs_site.py:218`

### The Examples page lists the project's best asset and links none of it [BAD] (severity: medium)
Four bullet titles, no URLs to GitHub or anywhere else. The examples are the strongest onboarding material AgentDeck has and the page that exists to route readers to them is a dead end. Only Jack, documented elsewhere, gets a link.
```python
- **Chat Agent with a Tool:** Basic tool execution and streaming.
- **Workflow with Human Approval:** Pausing for approval before high-stakes actions.
- **Agent with a Skill:** Extending agent capability using domain skills.
- **Existing LangGraph Agent:** Wrapping a LangGraph graph in AgentDeck.
```
Evidence: `docs-site/content/examples/index.mdx:7`

### The CLI is one command with no description and no way to find its argument [BAD] (severity: medium)
`agentdeck --help` prints `{runs}` and nothing else: no parser has a `description=`. `--control-db` is required with no default and no fallback to `AGENTDECK_CONTROL`, and there is no `runs list`, so the terminal cannot tell you either the run id or the database path it demands. `docs/design/agentdeck-v2-architecture.md:863` promised `agentdeck run` and `agentdeck sessions replay`; neither shipped.
```python
parser = argparse.ArgumentParser(prog="agentdeck")
subcommands = parser.add_subparsers(dest="resource", required=True)
runs = subcommands.add_parser("runs")
runs_commands = runs.add_subparsers(dest="action", required=True)
signal_cmd = runs_commands.add_parser("signal")
```
Evidence: `agentdeck/cli.py:31`
Ref: https://docs.python.org/3/library/argparse.html#description

### No scaffolding command for the layout the README calls the registration [BAD] (severity: medium)
`.agentdeck/` is the product's central opinion and it must be created by hand from a README tree diagram, with directory names, filenames and a module-level variable all load-bearing and each one a separate `ConfigError`. A framework that owns a project layout normally ships a generator for it; here the only path to a correct one is copying `examples/`. (Competitor CLI scaffolding: UNVERIFIED.)
```python
.agentdeck/
├── agents/greeter/agent.py            # an Agent(...)
├── workflows/new_booking/workflow.py  # a Workflow(...)
└── skills/parse-request/              # SKILL.md + optional scripts
```
Evidence: `README.md:135`
Ref: https://langchain-ai.github.io/langgraph/

### Four majors in three weeks against an 8-line migration page [BAD] (severity: medium)
`1.0.0` shipped 2026-07-27 and `4.0.0` on 2026-08-16, 20 days later. The README says breaking changes are listed in the CHANGELOG, where `4.0.0` is 366 lines of prose. The docs site's Migration Guides page offers two bullets and no version pair.
```python
## Upgrading to Current Release

- `Deck` is the single composition root.
- Ensure all environment variables use the `AGENTDECK_*` prefix.
```
Evidence: `docs-site/content/resources/migration-guides.mdx:5`
Ref: https://keepachangelog.com/

### Project configuration lives outside the project directory [BAD] (severity: low)
The README's claim is that everything you define lives in `.agentdeck/`. `.mcp.json` is a sibling of it, and `config.yaml`/`.env` resolve from `Path.cwd()`, so `Deck.from_project("other/path")` reads its catalog from one place and its configuration from another. The docstring documents the trap rather than closing it.
```python
# ``.mcp.json`` lives at the project root  -  a sibling of ``.agentdeck/``, not inside it.
# For the default ``path`` this is also where ``config.yaml``/``.env`` resolve from
# (both read off ``Path.cwd()``); an explicit non-default ``path`` only matches that if
# the caller also runs from its parent.
```
Evidence: `agentdeck/deck.py:470`

### Small stale references in otherwise excellent docs [BAD] (severity: low)
Known Issues is pinned to "open against **v4.0.0**" five patch releases later, so a reader cannot tell which entries survived. The executable-fence test cites `getting-started.mdx` twice as the page documenting `OPENAI_USE_RESPONSES`; that page does not exist under `docs-site/content/`.
```python
knobs getting-started.mdx tells a reader to set for a non-OpenAI endpoint. Nothing in
``agentdeck`` is patched: this is the path a reader's own shell actually takes, ...
```
Evidence: `tests/test_docs_examples.py:17`

## Bottom line

Where AgentDeck's docs are generated from code or written by whoever built the thing, they are excellent: the examples, the settings and Deck reference, the error taxonomy, the CHANGELOG and Known Issues all clear the OpenAI Agents SDK and LangGraph bar, and the anti-rot test suite is better than either. Where the docs were filled in to complete a nav tree, they are worse than absent: 15 stub pages, three of which are the destinations the project's own error messages send you to, and one of which invents a setting that has already leaked into the LLM-facing corpus. The single highest-leverage fix is the Quickstart, which currently cannot produce the output it shows, is missing the two environment variables it depends on, and is the one page every new user reads first.
