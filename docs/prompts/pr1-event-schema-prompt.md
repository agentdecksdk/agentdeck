# Prompt — PR #1: The Event Schema (`core/events.py` + `core/content.py`)

Prerequisite: PR #0 (golden baselines + import-linter) is merged. Copy everything below
the line into Claude Code at the repo root.

---

You are working in `Sagi5060/agentdeck` (Python 3.11+, pydantic v2). PR #0 established
golden wire baselines and a staged import-linter contract. **This PR creates the single
most durable artifact of the entire refactor: the canonical event schema.** Everything —
engines, protocols, dashboards, audit — will read these shapes forever. The schema below
is the reviewed contract: implement it exactly; where it is silent, choose the most
boring option and flag the choice in the PR description. Do NOT improve, extend, or
"future-proof" beyond what is written.

## Task

One PR titled `feat(core): canonical event schema v1`. It creates the `agentdeck/core/`
package containing exactly `events.py`, `content.py`, and `__init__.py`, plus tests, and
activates the staged import-linter contract for `core/`. Nothing else in the codebase
imports `core/` yet; behavior of the running system is unchanged by construction.

## The contract to implement

### Envelope — exactly eight fields, closed

Every event is `Event` with a **nested** payload (envelope fields and payload fields
never share a namespace):

```python
class Event(BaseModel):
    v: int = 1                    # schema version; breaking changes bump this
    kind: str                     # payload discriminator, e.g. "text.delta"
    seq: int                      # per-run, contiguous from 0, assigned by the Runtime
    run_id: str
    session_id: str | None
    tenant: str
    origin: str                   # name of the invocable that produced it, never the engine
    ts: AwareDatetime             # informational; ordering authority is seq, never ts
    payload: <discriminated union by kind>
```

The envelope is **closed** (rule D9): implement no other envelope fields, and add a
docstring stating that new needs go into payloads or into `run.started`, and that an
envelope addition requires demonstrating routing/ordering/isolation is impossible
without it.

### Content blocks (`content.py`)

```python
TextBlock      {type:"text", text:str}
ImageBlock     {type:"image", media_type:str, data_b64:str}
ResourceBlock  {type:"resource", uri:str, media_type:str|None}
ContentBlock = discriminated union on "type"
Input = list[ContentBlock]
```

Provide `coerce_input(value: str | Input) -> Input` (a bare string becomes
`[TextBlock]`; an `Input` passes through unchanged; anything else raises). Guard against
double-wrapping in a test.

### Payload catalog — implement all of these, exactly

Naming rule: dot-case `noun.past_tense`. Every payload class carries
`kind: Literal["..."]` as its discriminator.

**Lifecycle**

- `run.started` — `{invocable: str, kind_of_invocable: Literal["agent","workflow","skill"], parent_run_id: str|None, input: Input, context: RunContextSnapshot}` where `RunContextSnapshot = {principal: str, trace_id: str, budget: {max_usd: float|None, max_tokens: int|None}|None, triggered_by: str|None}`. This event is the join point: per-run constants live here, never on the envelope.
- `run.completed` — `{output: Input, usage: Usage}` where `Usage = {input_tokens:int, output_tokens:int, usd:float|None}`. The usage here is the **authoritative aggregate**.
- `run.failed` — `{error_code: Literal["engine_error","tool_error","budget_exceeded","deadline","cancelled_hard"], message: str, retryable: bool}`.
- `run.paused`, `run.resumed`, `run.cancelled` — `{reason: str|None}`.
- `run.interrupted` — `{interrupt_id: str, reason: Literal["human","pause","approval"], payload: dict, thread_id: str|None, expected_resume: str|None}`.

**Content**

- `text.delta` — `{message_id: str, text: str}`.
- `thought.delta` — `{message_id: str, text: str}`.
- `message.completed` — `{message_id: str, text: str}`. Carries the **full final text**
  (decision B): deltas are streaming UX; this event is the record. Document in the class
  docstring: one `message_id` never spans two origins.

**Tools**

- `tool.call.started` — `{call_id: str, tool: str, args: dict}`.
- `tool.call.completed` — `{call_id: str, tool: str, result_preview: str, result_size: int, result_sha256: str, artifact_id: str|None, error: str|None}`. Preview is capped (define `RESULT_PREVIEW_MAX = 4096` and enforce it in a validator). Raw full results never appear in events.

**Workflow / data / control**

- `node.updated` — `{node: str, state_patch: dict}` (shallow-merge semantics; say so in the docstring).
- `artifact.created` — `{artifact_id: str, media_type: str, uri: str, size: int}`. By reference only, never inline bytes.
- `usage.reported` — `{model: str, usage: Usage}` (per-model-call, advisory; the terminal aggregate is authoritative).
- `input.appended` — `{input: Input, source: str}` (mid-turn steering; schema now, no producer yet).
- `custom` — `{name: str, data: dict}` where `name` MUST be namespaced (`"<namespace>.<event>"`; validate the dot). Include rule D10 in the module docstring: kinds are minted only in core; engines translate into existing kinds or use namespaced `custom`; recurring `custom` usage is a promotion signal, not a precedent.

### Forward compatibility — the load-bearing mechanism

Pydantic discriminated unions reject unknown discriminators by default, which is the
opposite of the requirement. Implement:

```python
class UnknownEvent(BaseModel):
    kind: str
    raw_payload: dict
```

and a single parsing entry point `parse_event(data: dict) -> Event` that: parses known
kinds into their typed payloads; on an unknown `kind`, returns an Event whose payload is
`UnknownEvent` (envelope still fully validated); tolerates unknown **fields** inside
known payloads (`model_config = ConfigDict(extra="ignore")` or equivalent — pick one,
apply uniformly). Consumers skip `UnknownEvent`; stores persist it.

### Invariants module

In `events.py` (or `core/invariants.py` if cleaner), pure functions the future contract
suite will import — no I/O:

- `check_contiguous(events) -> list[int]` — missing seq numbers for one run.
- `check_terminal(events) -> None|str` — exactly one terminal kind
  (`run.completed|failed|cancelled`) and it is last; return a violation description.
- Document (docstrings, tested where testable here): seq is assigned only by the
  Runtime; persist-before-yield; an interrupted-then-resumed run keeps the same
  `run_id` with continuing seq.

## Tests (the bulk of the PR)

1. Round-trip every kind: construct → `model_dump_json` → `parse_event` → equal.
2. Forward-compat: an event with `kind: "future.thing"` and an unknown field inside a
   known payload both parse without raising; the first yields `UnknownEvent`; a toy
   consumer loop skips it and processes the rest.
3. Discriminator integrity: payload `kind` literal must match envelope `kind` (validator
   + test for the mismatch case).
4. `coerce_input` cases including the double-wrap guard.
5. Preview cap enforced; `custom` namespace validation; `run.failed` error codes are the
   closed set.
6. Invariant helpers: gap detection finds a planted gap; terminal check catches
   zero-terminal, double-terminal, and terminal-not-last.
7. Golden JSON: commit one serialized example per kind under
   `tests/core/snapshots/`; a test asserts current serialization matches byte-for-byte
   (this freezes the wire shape — future diffs to these files ARE schema changes and
   must say so).

## Guardrails and scope

- Activate the staged import-linter contract: `agentdeck.core` may import only stdlib
  and pydantic. Red-test it (add a forbidden import, show the failure in the PR
  description, revert).
- Out of scope, do not create: `RunContext`/`context.py`, `status.py`, any `ports/`,
  Runtime, any consumer or producer wiring, any change to existing modules. Nothing
  outside `agentdeck/core/` and `tests/` may appear in the diff, except the linter
  config activation.
- Module docstring in `events.py` records the evolution rules (D8): adding a kind or an
  optional field = minor; renaming/removing/changing meaning = bump `v`; consumers must
  ignore unknown kinds; plus D9 and D10 as written above.

## Definition of done

Full existing suite green and untouched; all new tests green; golden JSON per kind
committed; linter contract active with red-test evidence in the PR description; PR
description lists every place you made a judgment call the spec didn't dictate, however
small — those items are the review agenda.
