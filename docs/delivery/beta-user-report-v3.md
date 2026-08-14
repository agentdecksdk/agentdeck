# First beta-user report — agentdeck v3 (`Deck`)

**Date:** 2026-08-10 · **Build:** `feat/164-deck-composition-api` (PR #165) · **Method:** fresh
venv outside the repo, `pip install -e ".[serve,durability]"`, a project written from scratch, real
model calls.

**Bias disclosure:** I reviewed this code before using it. Places where knowing the internals
rescued me are flagged **[insider save]** — each is where a genuine newcomer would have been stuck.

**Provider note:** the workspace `.env` pointed `OPENAI_BASE_URL` at a Gemini-compatible proxy, not
OpenAI. An accident, but it turned the session into a portability test; findings that are the
provider's fault are marked as such.

## Verdict

**The core loop is genuinely good.** Twenty-two lines, no config file, no project directory, a real
answer, first try, cold:

```python
from agentdeck import Agent, Deck

greeter = Agent(name="greeter", instructions="You are terse.")
deck = Deck(agents=[greeter])

async with deck:
    result = await deck.run("greeter", "What is the capital of France?")
    print(result.output)   # "Paris is the capital of France."
```

**But the single most-advertised line in the API does not work.** `tools=[find_slots]` with a plain
callable — the exact form in the issue, the plan, and the class docstring — fails at run time, and
it is the second thing everyone does.

Would I ship this as a beta? **Yes, after B1 and B2.** The foundation is sound; the defects are at
the edges where a user meets it.

## Blocking

### B1 — `tools=[plain_callable]` does not work, and `build()` says nothing

The documented shape is `tools=[find_slots, book_slot]` — "plain callables, compiled per engine".
What happens:

```
agents.exceptions.UserError: Hosted tools are not supported with the ChatCompletions API.
Got tool type: <class 'function'>, tool: <function find_slots at 0x7fe651538c20>
```

Wrapping in the SDK's own `@function_tool` works perfectly — multi-turn tool use, two tools,
correct arguments. So the gap is a missing wrap in the compile step, or three wrong sentences in
the docs. Two defects, and the second is worse: plain callables are passed to the SDK raw, and
**`build()` accepts them silently** — validation is `build()`'s entire stated purpose, yet a deck
full of unusable tools builds clean and dies at run time from inside the SDK, with a message about
*hosted tools* that points at nothing a user recognises. A newcomer following the README hits this
in minute two with no path from the error to the cause. **[insider save]** — I only knew to try
`@function_tool` because I had read the compile step.

### B2 — MCP prints a "booting without it" warning during `build()`

With a valid `.mcp.json` containing `calendar`, and `Agent(mcp=["calendar"])`:

```
MCP server 'calendar' not found in config; agent boots without it. Configured: []
```

`build()` passes and `MCP.names()` resolves `calendar` correctly; the servers legitimately do not
start until `__aenter__`, so the warning describes a state that is not yet meant to exist. The
message is alarming and wrong at that moment — and it is the *exact wording of the silent-drop
behaviour* the plan set out to eliminate ("an unknown name is a `build()` error rather than the
current silent drop"), so the one visible signal a user gets says the old behaviour is still there.

## Non-blocking

| # | Finding | Evidence |
|---|---|---|
| N1 | `agentdeck.__version__` does not exist — the first line anyone types after installing | `AttributeError` |
| N2 | Every streamed event prints as `Event`, so there is nothing obvious to `isinstance`/`match` on, and nothing in the object's surface suggests what to switch on instead | `print(type(event).__name__)` → `Event, Event, Event, Event, Event` |
| N3 | An empty `Deck()` builds happily and can run nothing | `Deck().build()` succeeds; `deck.run(anything)` can never resolve. Already filed as **#167** — using it confirmed it is worth doing |
| N4 | A declaration that is never instantiated vanishes silently — the one migration trap in the release, since under v1 a bare subclass *was* the agent, so a porter writes exactly this and gets an empty deck with no clue why | a bundle with only `class Ghost(AgentDeclaration)` yields `from_project agents: {}`, no error, no warning |
| N5 | `usage.usd` is always `None` — a field that exists and is never populated, which is worse than not having it | `input_tokens=19 output_tokens=7 usd=None` |
| N6 | Handoffs fail against a non-OpenAI provider *(provider's fault, ours to survive)* — surfaced as a raw provider traceback with no hint that handoffs are the trigger. Worth a known-limitations note, since "OpenAI-compatible endpoint" is how most people point this at anything else | Gemini rejects the assistant-terminated history a handoff produces: `openai.BadRequestError: 400 — Please ensure that single turn requests end with a user role` |

## What is genuinely good — do not touch these

**Error messages, where they exist, are better than most shipped SDKs.** Each names the problem,
the location, and the fix:

```
this Deck is not open: use `async with deck:` (or `await deck.__aenter__()`) first.
two entries in agents= both use the name 'd'; one name is one invocable — rename one of them.
SKILL.md: frontmatter declares name 'WRONG', which must match its directory name 'broken'.
Agent is immutable; build a new one instead of setting 'name'.
```

- **`base=` inheritance behaves exactly as specified.** A shared `AgentDeclaration` propagates
  `instructions` and `model`; an explicit `tools=[]` overrides to empty rather than falling back, and
  the `_UNSET`-versus-falsy distinction is invisible in use.
- **Immutability is real and the error explains itself** — `booking.name = "x"` raises rather than
  silently corrupting a compiled catalog.
- **`skills=` and `mcp=` coercion is seamless** — bare path and wrapper object produce identical
  results.
- **Tool calling, once wrapped, is flawless** — a two-tool booking agent chained `find_slots` then
  `book_slot` with correct arguments and no prompting tricks.
- **`build()` catches the right things when it catches them** — duplicate names, unknown skills,
  frontmatter mismatches — with both offenders named.

**Ranked, if I could fix five things:** B1 (make plain callables work, or stop advertising them,
then make `build()` reject a tool it cannot compile), B2, N4, N2, N1. Everything else is polish.
The architecture underneath is doing its job: I never once had to think about the Runtime, the
store, or an engine.
