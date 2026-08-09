# Plan — skills as the `SKILL.md` directory protocol

**Status:** proposed · **Date:** 2026-08-09 · **Target: v3.0.0 (breaking)**
Third of three: pairs with `plan-phase4-deck.md` (which owns `skills=` and `build()`) and
`plan-context-injection.md` (which owns `Context[T]`). Resolves that plan's one open ruling.

## The rule

**A skill is progressive knowledge disclosure inside the current run.** It is not a run, not a
process, and not a program. Activating one adds instructions and resources to the agent
execution already in flight — same run, session, context, reporter and gate.

## Rulings taken (2026-08-09)

| # | Question | Ruling |
|---|---|---|
| 1 | Workflows have no model to disclose to — what replaces `SkillNode`? | **Nothing.** `SkillNode` is deleted. A workflow that needs a skill uses an **agent node whose agent declares `skills=[…]`**; the skill activates inside that agent's execution. One meaning for the word, everywhere. |
| 2 | Multiple skill roots vs the SDK's single `skills_path` | **One lazy source per root, merged into one registry.** A duplicate name across roots is a `build()` error naming both paths. |
| 3 | `scripts/` | **Deferred from v3.** Documented as reserved and inert. The serializable-projection boundary gets decided when there is a real script to run. |

## Most of this is already native — do not rebuild it

`agents/capabilities/skills.py` already wires the SDK's `LocalDirLazySkillSource`, which
implements the required build behavior exactly:

```
list_skill_metadata()  ->  SkillMetadata(name, description, path)   # metadata only, for discovery
load_skill(name)       ->  full content                             # on activation
```

It scans `<root>/<name>/SKILL.md`, is lazy by construction, and `build_skills(names, dir)`
already gates by an allow-list and already raises on an unknown name. CLAUDE.md's Native-First
rule applies: AgentDeck supplies the *roots* and the *registry*, and the SDK supplies the
disclosure.

What is actually missing is small: multiple roots, the roots coming from `Deck(skills=…)`
instead of `.agentdeck/skills`, and validation moving into `deck.build()`.

## What gets built

**A merged skill registry.** One lazy source per configured root; metadata merged into one
name-keyed registry at `build()`. Only metadata is read then — full content is loaded when a
skill is activated, which is the SDK's behavior already.

**`Deck(skills=…)`** accepting a path or paths, feeding that registry.

**`Agent(skills=["booking"])`** resolving names against it, unchanged in mechanism from today's
allow-list.

**`build()` validation:** every declared name resolves; duplicate names across roots raise and
name both paths — one name is one skill, the same rule `PluginRegistry` applies to bundles.

## What gets deleted

The executable-skill model, in full:

```
agentdeck/skills/executor.py        246   SkillExecutor — the sandboxed subprocess runner
agentdeck/skills/output.py           98   SkillOutputSchema — parsing stdout into workflow state
agentdeck/skills/skill_runtime/       55   the in-sandbox package
agentdeck/workflows/nodes.py          –   SkillNode (the class only)
agentdeck/skills/bundle.py            –   SkillBundle; SkillRegistry survives, reshaped
```

**No test references `SkillExecutor`, `SkillNode` or `SkillOutputSchema`, and the golden fixture
project contains no `SKILL.md`.** A 246-line sandboxed executor with no coverage is its own
finding; it also means this deletion cannot silently break a tested behavior.

Two consequences to handle rather than discover:

- `runtime/capture.py` (`CAPTURE_ENV`, `Capture`) exists as the host↔sandbox wire contract for
  skill subprocesses. With the subprocess gone it has no second end. It should go with them —
  check for other readers first.
- `Settings.sandbox_env()` composes `OPENAI_*` + `SKILL_*` for skill subprocesses. `SKILL_*`
  loses its only consumer, which strengthens the case in **#155** for prefixing or removing it.

## Context, and why nothing special is needed

A skill is disclosed *into* an execution that already has a context, so there is nothing to
propagate and nothing to pass:

```
deck.run(..., context=MiddleContext(...))
   -> agent execution holds Context[MiddleContext]
      -> SKILL.md activated: instructions + resources appended
         -> tools called from there get the same Context[T] by injection
         -> a workflow triggered from there inherits the same context
```

`activate_skill(..., context=…)` never exists. A skill does not own or construct a context.

**`SKILL.md` stays portable.** No `{{ context.customer }}`, no AgentDeck-specific templating.
Application values reach the model only through dynamic instructions, which return exactly what
they choose to expose:

```
base agent instructions
+ dynamic context-aware instructions   (only what the function returns)
+ activated SKILL.md instructions      (portable, context-free)
```

That is a security property: the context is never dumped into a prompt, and a skill file cannot
reach into it.

## Sequencing

Depends on phase 4b (`Deck(skills=…)` and `build()`). Order within this plan:

1. Merged multi-root registry, replacing `SkillRegistry(src)`
2. `Deck(skills=…)` wiring and `build()` validation
3. Delete the executable path and its two orphaned dependencies
4. Rewrite `workflows` docs/examples that used `SkillNode` onto an agent node
5. `scripts/` documented as reserved

## Risks

- **The deletion is larger than it looks.** `CAPTURE_ENV` and `SKILL_*` settings are load-bearing
  only for the subprocess; removing the subprocess without them leaves dead wire contract.
- **Workflow examples change shape.** Any `.agentdeck/workflows/*` using `SkillNode` is rewritten
  as an agent node. That is a user-visible migration and belongs in the v3 guide.
- **`references/` and `assets/` are disclosed by the SDK, not by us.** Whatever it does with
  those directories is the contract; this plan should not invent a second one. Verify before
  documenting them as supported.
