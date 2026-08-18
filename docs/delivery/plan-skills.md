# Plan  -  skills as the `SKILL.md` directory protocol

**Delivered** in v3.0.0 · **Date:** 2026-08-09 · Third of three: pairs with `plan-phase4-deck.md`
(which owns `skills=` and `build()`) and `plan-context-injection.md` (which owns `Context[T]`, and whose
one open ruling this resolves).

**A skill is progressive knowledge disclosure inside the current run.** Not a run, not a process, not a
program: activating one adds instructions and resources to the agent execution already in flight  -  same
run, session, context, reporter and gate.

## Rulings taken (2026-08-09)

| # | Question | Ruling |
|---|---|---|
| 1 | Workflows have no model to disclose to  -  what replaces `SkillNode`? | **Nothing.** `SkillNode` is deleted; a workflow that needs a skill uses an **agent node whose agent declares `skills=[…]`**, and the skill activates inside that agent's execution. One meaning for the word, everywhere |
| 2 | Multiple skill roots vs the SDK's single `skills_path` | **One lazy source per root, merged into one registry.** A duplicate name across roots is a `build()` error naming both paths |
| 3 | `scripts/` | **Deferred from v3.** Documented as reserved and inert |
| 4 | Root scanning depth | **Direct-child only**  -  `<root>/<name>/SKILL.md`, never recursive. Matches the SDK and keeps shadowing predictable |
| 5 | Frontmatter | **`build()` validates it**, it does not merely find the file  -  the SDK is deliberately permissive and will not |
| 6 | Sandboxing | **A future ability, currently disabled and out of scope (#163).** Nothing a skill causes runs isolated in v3, which is why the executable path is deleted rather than made optional |

## Most of this is already native  -  do not rebuild it

`agents/capabilities/skills.py` already wires the SDK's `LocalDirLazySkillSource`, which is the required
build behavior exactly: `list_skill_metadata()` returns `SkillMetadata(name, description, path)` for
discovery and `load_skill(name)` the full content on activation, scanning `<root>/<name>/SKILL.md`,
lazy by construction, with `build_skills(names, dir)` already gating by allow-list and raising on an
unknown name. Per CLAUDE.md's Native-First rule, AgentDeck supplies the *roots* and the *registry*, the
SDK the disclosure. What is missing is small: multiple roots, roots from `Deck(skills=…)` instead of
`.agentdeck/skills`, and validation in `deck.build()`.

## What gets built

- **A merged skill registry**  -  one lazy source per configured root, metadata merged into one name-keyed registry at `build()`. Only metadata is read then; full content loads on activation, as the SDK already does.
- **`Skills(...)`**, the capability object owning roots, discovery, validation and indexing so those options grow there rather than on `Deck` (`deck-capability-wrapper-pattern.md`). `Deck(skills="./skills")` coerces a bare path into it. It is a **declaration**: constructing it reads nothing, discovery happens at `deck.build()`, which keeps `build()` the one place a bad skill directory is reported.
- **`Agent(skills=["booking"])`** resolving names against it, unchanged in mechanism from today's allow-list.
- **`build()` validation**  -  every declared name resolves; duplicate names across roots raise and name both paths, one name being one skill, the same rule `PluginRegistry` applies to bundles.

**And it validates frontmatter, because the SDK will not.** `list_skill_metadata` falls back to the
directory name, defaults a missing description to `"No description provided."`, and skips a malformed
file with `except OSError: continue`  -  two failures that are silent at runtime instead of loud at build.
A `SKILL.md` declaring `name: reservation` under `skills/booking/` registers as **`reservation`**, so
`Agent(skills=["booking"])` never matches; the Agent Skills spec requires `name` to match the parent
directory, and `build()` should enforce that. And the placeholder description is the text the model reads
when deciding whether to activate a skill  -  a skill nobody can choose is worse than one that fails to load.

## What gets deleted

The executable-skill model, in full:

| file | lines | what it was |
|---|---|---|
| `agentdeck/skills/executor.py` | 246 | `SkillExecutor`, the sandboxed subprocess runner |
| `agentdeck/skills/output.py` | 98 | `SkillOutputSchema`, parsing stdout into workflow state |
| `agentdeck/skills/skill_runtime/` | 55 | the in-sandbox package |
| `agentdeck/workflows/nodes.py` |  -  | `SkillNode` (the class only) |
| `agentdeck/skills/bundle.py` |  -  | `SkillBundle`; `SkillRegistry` survives, reshaped |

**No test references `SkillExecutor`, `SkillNode` or `SkillOutputSchema`, and the golden fixture project
contains no `SKILL.md`**  -  a 246-line sandboxed executor with no coverage is its own finding, and it
means this deletion cannot silently break a tested behavior. Two consequences to handle rather than
discover: `runtime/capture.py` (`CAPTURE_ENV`, `Capture`) is the host↔sandbox wire contract for skill
subprocesses and loses its second end, so it goes with them once other readers are ruled out; and
`Settings.sandbox_env()`'s `SKILL_*` half loses its only consumer, which strengthens **#155**'s case for
prefixing or removing it.

## Context, and why nothing special is needed

A skill is disclosed *into* an execution that already has a context, so there is nothing to propagate
and nothing to pass  -  `activate_skill(..., context=…)` never exists, and a skill neither owns nor
constructs a context:

```
deck.run(..., context=MiddleContext(...))
   -> agent execution holds Context[MiddleContext]
      -> SKILL.md activated: instructions + resources appended
         -> tools called from there get the same Context[T] by injection
         -> a workflow triggered from there inherits the same context
```

**`SKILL.md` stays portable**  -  no `{{ context.customer }}`, no AgentDeck-specific templating. The prompt
is `base agent instructions` + `dynamic context-aware instructions` (only what the function returns) +
`activated SKILL.md instructions` (portable, context-free), so application values reach the model only
through dynamic instructions. That is a security property: the context is never dumped into a prompt,
and a skill file cannot reach into it.

## Sequencing

Depends on phase 4b (`Deck(skills=…)` and `build()`), then: 1 the merged multi-root registry replacing
`SkillRegistry(src)` · 2 `Deck(skills=…)` wiring and `build()` validation · 3 delete the executable path
and its two orphaned dependencies · 4 rewrite `workflows` docs/examples that used `SkillNode` onto an
agent node · 5 document `scripts/` as reserved.

## Risks

- **The deletion is larger than it looks.** `CAPTURE_ENV` and `SKILL_*` settings are load-bearing only for the subprocess; removing the subprocess without them leaves a dead wire contract.
- **Workflow examples change shape.** Any `.agentdeck/workflows/*` using `SkillNode` is rewritten as an agent node  -  a user-visible migration that belongs in the v3 guide.
- **Sandboxing is disabled, not preserved.** The executable path is deleted outright rather than kept behind a flag: a disabled feature with live code is the thing #163 exists to redesign properly, and a half-wired subprocess would prejudge that design.
- **`references/` and `assets/` are disclosed by the SDK, not by us.** Whatever it does with those directories is the contract; verify before documenting them as supported.
