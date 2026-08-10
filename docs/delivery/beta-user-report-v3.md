# First beta-user report — agentdeck v3 (`Deck`)

**Date:** 2026-08-10 · **Build:** `feat/164-deck-composition-api` (PR #165) · **Method:** fresh
venv outside the repo, `pip install -e ".[serve,durability]"`, a project written from scratch,
real model calls.

**Bias disclosure:** I reviewed this code before using it. Every place where knowing the
internals rescued me is flagged **[insider save]** — each one is a place a genuine newcomer
would have been stuck.

**Provider note:** the key in the workspace `.env` sets `OPENAI_BASE_URL` to a
Gemini-compatible proxy, not OpenAI. That was an accident, but a useful one: it turned the
session into a portability test. Findings that are the provider's fault and not agentdeck's are
marked as such.

---

## Verdict

**The core loop is genuinely good.** Twenty-two lines, no config file, no project directory, a
real answer:

```python
from agentdeck import Agent, Deck

greeter = Agent(name="greeter", instructions="You are terse.")
deck = Deck(agents=[greeter])

async with deck:
    result = await deck.run("greeter", "What is the capital of France?")
    print(result.output)   # "Paris is the capital of France."
```

That worked first try, cold. The composition story the plan promised is real and it is pleasant.

**But the single most-advertised line in the API does not work.** `tools=[find_slots]` with a
plain callable — the exact form in the issue, the plan, and the class docstring — fails at run
time. That is a blocker for a beta, because it is the second thing everyone does.

Would I ship this as a beta? **Yes, after B1 and B2.** The foundation is sound; the defects are
at the edges where a user meets it.

---

## Blocking

### B1 — `tools=[plain_callable]` does not work, and `build()` says nothing

The documented shape:

```python
tools=[find_slots, book_slot],   # plain callables, compiled per engine
```

What happens:

```
agents.exceptions.UserError: Hosted tools are not supported with the ChatCompletions API.
Got tool type: <class 'function'>, tool: <function find_slots at 0x7fe651538c20>
```

Wrapping in the SDK's own `@function_tool` works perfectly — multi-turn tool use, two tools,
correct arguments. So the gap is a missing wrap in the compile step, or three wrong sentences in
the docs.

Two separate defects, and the second is worse than the first:

1. Plain callables are passed to the SDK raw.
2. **`build()` accepts them silently.** Validation is `build()`'s entire stated purpose — "every
   agent/workflow compiles to an `InvocableSpec`" — yet a deck full of unusable tools builds
   clean and dies at run time, from inside the SDK, with a message about *hosted tools* that
   points at nothing a user recognises.

A newcomer following the README hits this in minute two and has no path from the error to the
cause. **[insider save]** — I only knew to try `@function_tool` because I had read the compile
step.

### B2 — MCP prints a "booting without it" warning during `build()`

With a valid `.mcp.json` containing `calendar`, and `Agent(mcp=["calendar"])`:

```
MCP server 'calendar' not found in config; agent boots without it. Configured: []
```

`build()` passes — `MCP.names()` resolves `calendar` correctly — but the compile step warns that
the server is missing and the agent will boot without it. The servers legitimately do not start
until `__aenter__`, so the warning is describing a state that is not yet meant to exist.

Two problems. The message is alarming and wrong at that moment. And it is the *exact wording of
the silent-drop behaviour* the plan set out to eliminate ("an unknown name is a `build()` error
rather than the current silent drop") — so the one visible signal a user gets says the old
behaviour is still there, whether or not it is.

---

## Non-blocking

### N1 — `agentdeck.__version__` does not exist

The first line anyone types after installing. `AttributeError`.

### N2 — Every streamed event prints as `Event`

```python
async for event in deck.stream("booking", "Say hello."):
    print(type(event).__name__)     # Event, Event, Event, Event, Event
```

There is no way to tell a text delta from a run-completed by class. A user's first instinct —
`isinstance` or `match` on the type — does not work, and nothing in the object's surface
suggests what to switch on instead. Whatever the discriminator is, it needs to be the obvious
thing.

### N3 — An empty `Deck()` builds happily and can run nothing

`Deck().build()` succeeds. `deck.run(anything)` can never resolve. Already filed as **#167**;
using it confirmed it is worth doing.

### N4 — A declaration that is never instantiated vanishes silently

```python
# .agentdeck/agents/ghost/agent.py
class Ghost(AgentDeclaration):
    instructions = "boo"
```

```
from_project agents: {}
```

No error, no warning — the bundle directory simply contributes nothing. This is the one
migration trap in the whole release: under v1 a bare subclass *was* the agent, so anyone porting
a project writes exactly this and gets an empty deck with no clue why. The reviewer flagged it as
an edge case; using it, I would raise it — a bundle that imports cleanly and yields no instance
should say so.

### N5 — `usage.usd` is always `None`

`input_tokens=19 output_tokens=7 usd=None`. The field exists and is never populated, which is
worse than not having it.

### N6 — Handoffs fail against a non-OpenAI provider *(provider's fault, ours to survive)*

```
openai.BadRequestError: 400 — Please ensure that single turn requests end with a user role
```

Gemini rejects the assistant-terminated history a handoff produces. Not an agentdeck bug, but
agentdeck surfaces it as a raw provider traceback with no indication that handoffs are the
trigger. Worth a known-limitations note at minimum, since "OpenAI-compatible endpoint" is how
most people will point this at anything else.

---

## What is genuinely good — do not touch these

**Error messages, where they exist, are excellent.** These are better than most shipped SDKs:

```
this Deck is not open: use `async with deck:` (or `await deck.__aenter__()`) first.
two entries in agents= both use the name 'd'; one name is one invocable — rename one of them.
SKILL.md: frontmatter declares name 'WRONG', which must match its directory name 'broken'.
Agent is immutable; build a new one instead of setting 'name'.
```

Each names the problem, the location, and the fix. The lifecycle error in particular converts a
confusing failure into a one-line correction.

**`base=` inheritance behaves exactly as specified.** A shared `AgentDeclaration` propagates
`instructions` and `model`; an explicit `tools=[]` overrides to empty rather than falling back to
the base. The `_UNSET`-versus-falsy distinction is invisible in use, which is the point.

**Immutability is real and the error explains itself.** `booking.name = "x"` raises rather than
silently corrupting a compiled catalog.

**`skills=` and `mcp=` coercion is seamless.** Bare path and wrapper object produce identical
results; I never once had to think about which form to use.

**Tool calling, once wrapped, is flawless.** A two-tool booking agent chained `find_slots` then
`book_slot` with correct arguments and no prompting tricks.

**`build()` catches the right things when it catches them** — duplicate names, unknown skills,
frontmatter mismatches — with both offenders named.

---

## Ranked, if I could fix five things

1. **B1** — make plain callables work, or stop advertising them. Then make `build()` reject a
   tool it cannot compile.
2. **B2** — do not warn about MCP servers during a phase that deliberately starts none.
3. **N4** — a bundle that yields no instance should fail loudly; this is the migration trap.
4. **N2** — give streamed events an obvious discriminator.
5. **N1** — add `__version__`.

Everything else is polish. The architecture underneath is doing its job: I never once had to
think about the Runtime, the store, or an engine, which is exactly what the plan set out to
achieve.
