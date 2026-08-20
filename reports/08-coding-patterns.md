# 08 - Cross-Cutting Coding Patterns

This report judges craft rather than any one area: whether the same problem is solved the same way in every module, whether names and comments carry their weight, and whether the typing, async, and defensive idioms are one house style or 100 personal ones. Measured over the 102 modules and 11,557 code lines under `agentdeck/`, against `docs/engineering/coding-standards.md` and general Python practice. The mechanical style rules hold absolutely (zero em dash characters in the package, `from __future__ import annotations` in all 78 non-`__init__` modules and none of the 24 `__init__` files), so everything below is about substance.

## Findings

### One frozen pydantic base for the whole schema, with the forward-compat reason on it [GOOD] (severity: high)
Thirty-three schema models inherit one base that fixes immutability and unknown-field policy in a single `ConfigDict`, and the docstring says why `extra="ignore"` rather than the usual `forbid`. Nothing downstream re-decides either question.
```python
class CoreModel(BaseModel):
    """Base for the schema models: unknown fields are dropped, and nothing mutates.

    Dropping them is forward compatibility  -  a field a newer writer added has to land, not
    raise. A model that is built rather than parsed sets ``extra="forbid"`` instead.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)
```
Evidence: `agentdeck/core/base.py:15-22`

### Policy lives in tables, and the tables are the house style [GOOD] (severity: high)
Twenty-one module-level mapping/set tables across 12 modules encode decisions that most codebases spell as `if` chains: signal to exception, invocable kind to engine, event kind to status, hook name to bridge. The comments name the discipline explicitly.
```python
# Which exception carries which effect  -  declared, not branched on, so the only place a verb is
# tested against a name is a table. ``POLICY`` says *whether* to halt; this says how.
_HALTED_BY: Mapping[Signal, type[ControlSignalled]] = {
    Signal.CANCEL: RunCancelledError,
    Signal.PAUSE: RunPausedError,
}
```
Evidence: `agentdeck/core/control.py:102-107`, `agentdeck/runtime/discovery.py:36-39`, `agentdeck/authoring/hooks.py:33`

### Every lint and type suppression carries its reason on the same line [GOOD] (severity: high)
All 7 `noqa` and 12 of the 13 `ty: ignore` comments append the justification inline, so a reviewer never has to reconstruct why a rule was waived. This is exactly the "narrow documented suppressions" the standards ask for, done without a single exception file. The scout inventory's "`type: ignore` = 0" line is an artifact of the project using `ty` rather than mypy: the real count is 13, and only `stores/postgres/store.py:334` is bare.
```python
class ControlSignalled(Exception):  # noqa: N818  -  not an error: a signal honored exactly as asked
```
Evidence: `agentdeck/core/control.py:55`, `agentdeck/adapters/engines/langgraph/checkpointer.py:97`, `agentdeck/core/ports/sink.py:24`

### The `ponytail:` shortcut convention is honored in all twelve places [GOOD] (severity: high)
Every one of the 12 markers states three things: what the shortcut is, the ceiling it works up to, and the concrete trigger that should force the upgrade. Not one is a bare "TODO: fix later" wearing a nicer name.
```python
# ponytail: every parked run's whole log, per call, and an approval inbox polls this  -
# so the cost is (parked runs x their length) on a path a UI hits on a timer. Fine while
# a deployment parks tens of runs; the upgrade is a store-side projection of each run's
# last interrupt, and the trigger is the first inbox that pages or that a poll can't
# answer inside its own refresh interval.
```
Evidence: `agentdeck/runtime/service.py:742-746`, `agentdeck/runtime/discovery.py:34-35`, `agentdeck/adapters/leases/sqlite/port.py:30-32`

### Async generator ownership is one idiom, applied everywhere [GOOD] (severity: high)
`contextlib.aclosing` wraps every generator handed across an ownership boundary, in 8 modules. There are only 5 `asyncio.create_task` calls in the whole package, each assigned to a named attribute or local, each with a reaper. No fire-and-forget tasks, no `ensure_future` scattering, no `TaskGroup`/`gather` mixing for the same job.
```python
task = asyncio.create_task(_drain(agen))
self._executions[opening.run_id] = task
task.add_done_callback(functools.partial(self._execution_done, opening.run_id))
```
Evidence: `agentdeck/deck.py:824-826`, `agentdeck/runtime/service.py:205-209`, `agentdeck/runtime/dispatch.py:304`

### A named predicate for "is this cancellation mine", used exactly where it is needed [GOOD] (severity: medium)
Four handlers in the dispatch swallow `CancelledError` to keep shutdown bounded, and all four gate the swallow on one helper instead of open-coding `current_task().cancelling()`. It is absent from `runtime/service.py` for the right reason: those two handlers re-raise unconditionally, so there is nothing to gate.
```python
def _cancelling_ourselves() -> bool:
    """True when the ``CancelledError`` in hand is one this task was asked to honour."""
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0
```
Evidence: `agentdeck/runtime/dispatch.py:79-87`, `agentdeck/runtime/dispatch.py:207`, `agentdeck/runtime/service.py:195-203`

### Not-found errors always list what does exist, and deep-link to the right page [GOOD] (severity: high)
Eight independent lookup sites in six modules all end with `Available: {sorted(...)}.`, so a typo answers itself. Separately, five modules compose their own `_XXX_DOCS` deep link off one `DOCS_URL` constant, so error text points at the specific page rather than a homepage.
```python
raise NotFoundError(f"No {self.label} named {name!r}. Available: {sorted(plugins)}.") from None
```
Evidence: `agentdeck/runtime/registry.py:72`, `agentdeck/deck.py:796`, `agentdeck/skills/__init__.py:87`, `agentdeck/errors.py:21`

### Logging is textbook library logging, with no house wrapper [GOOD] (severity: medium)
All 14 logger definitions are `logging.getLogger(__name__)`. Zero of the 53 log calls use an f-string: every one passes `%s` args for lazy formatting. No handler is installed, no root logger touched, no custom log facade invented.
```python
logger.warning(
    "sink %s did not take its backlog within %ss; %d events still queued", self._name, timeout, self.depth
)
```
Evidence: `agentdeck/runtime/dispatch.py:34`, `agentdeck/runtime/dispatch.py:197-202`

### Every timestamp is timezone-aware, and the one clock that matters is injected [GOOD] (severity: medium)
Zero uses of `datetime.utcnow()` and zero naive `datetime.now()` in the package; all five wall-clock reads pass `UTC`, and caller-supplied datetimes go through a `_require_aware` guard. Elapsed-time decisions use `time.monotonic` injected as a parameter, so the breaker cooldown is testable as a fact rather than a sleep.
```python
clock: Callable[[], float] = time.monotonic,
```
Evidence: `agentdeck/runtime/dispatch.py:111`, `agentdeck/deck.py:1025`, `agentdeck/adapters/stores/memory/store.py:22-23`

### The stated pydantic-versus-dataclass split is actually followed [GOOD] (severity: medium)
Boundaries are pydantic (6 `BaseModel` plus 33 `CoreModel` subclasses: events, content blocks, specs, settings, HTTP bodies). Internal value objects are `@dataclass(frozen=True, slots=True)`, 12 of them. The 8 `slots=True`-without-`frozen` dataclasses are not value objects at all: they are runners, registries, and open-trace bookkeeping, which mutate by design. The rule works because the exceptions are the right exceptions.
```python
@dataclass(frozen=True, slots=True)
class Context[T]:
```
Evidence: `agentdeck/core/context.py:119-120`, `agentdeck/core/status.py:62`, `agentdeck/runtime/registry.py:22-23`

### Control flow is flat, and the size distribution is healthy [GOOD] (severity: medium)
Of 648 functions, 310 have zero nested blocks and 219 have one: 82% at depth 1 or less, only 13 above depth 3. 551 of 648 are 25 lines or shorter, and exactly one exceeds 100. Guard clauses and early return are the default rather than an aspiration.
```python
if declared is None or analysis.context_parameter is None:
    return
required = analysis.context_type
if _satisfies(get_origin(declared) or declared, required) is not False:
    return
```
Evidence: `agentdeck/authoring/injection.py:179-183`

### No dumping-ground modules, and file names state the role [GOOD] (severity: medium)
Not one `utils.py`, `helpers.py`, `common.py`, `misc.py`, or `shared.py` in 102 modules. Every adapter follows the same two-axis naming: the directory names the technology, the file names the role, so `adapters/stores/redis/store.py` and `adapters/leases/sqlite/port.py` read identically without a reader knowing either. `core/base.py` is the one generic-sounding name and its docstring pins it to exactly two things.
```python
"""What every core model is built on: one base class, one JSON type.

Both are shared by modules with nothing else in common  -  content blocks, event payloads,
invocable specs, tool sets  -  so neither lives in whichever one needed it first.
"""
```
Evidence: `agentdeck/core/base.py:1-4`, `agentdeck/adapters/stores/redis/store.py`, `agentdeck/adapters/leases/sqlite/port.py`

### Context is added to a refused error without flattening its class [GOOD] (severity: medium)
Three compile-time validation sites need to prepend "which agent / node / hook" to an error raised deeper down. All three re-raise `type(refused)` and chain with `from refused`, so a `ContextTypeError` does not silently arrive as its `ConfigError` supertype. The idiom is right; the three-line comment explaining it is copy-pasted at each site instead of living in one named helper, which is the small blemish on an otherwise correct pattern.
```python
except ConfigError as refused:
    # Re-raised as its own class: a ContextTypeError flattened to its supertype here
    # would reach the caller as a different error than the one the API promises.
    raise type(refused)(f"node {name!r}: {refused}") from refused
```
Evidence: `agentdeck/authoring/graphs.py:74-77`, `agentdeck/authoring/compile.py:134-137`, `agentdeck/authoring/hooks.py:58-62`

### The wiring module raises `ValueError` where every peer raises `ConfigError` [BAD] (severity: medium)
`composition.py` has six raise sites for operator misconfiguration, all with perfectly `ConfigError`-shaped text naming the env var and the fix, and all raising bare `ValueError`. `deck.py` raises `ConfigError` 13 times, `mcp.py` 4, `registry.py` 4 for the same class of problem. A caller who catches the documented `ConfigError` around deck construction crashes on the single most likely misconfiguration: a bad `AGENTDECK_EVENTS` or `AGENTDECK_CONTROL` URL.
```python
raise ValueError(
    f"unknown event store scheme {scheme!r} in AGENTDECK_EVENTS={events.url!r}; expected "
    f"memory, sqlite, redis, rediss, or postgresql  -  see {_STORE_DOCS}"
)
```
Evidence: `agentdeck/composition.py:256-259`, `agentdeck/composition.py:177`, `agentdeck/composition.py:179-182`

### An import cycle inside the authoring ring, papered over with nine function-local imports [BAD] (severity: medium)
`compile.py` imports `Agent` and `Workflow` only under `TYPE_CHECKING`, then imports them for real inside function bodies; `agent.py` and `workflow.py` import `compile` inside four methods to get back. `deck.py` and `serve.py` do the same to each other. The cycle is real, and nine deferred imports are what keep it from raising at import time.
```python
def build(self) -> Any:
    from agentdeck.authoring.compile import compile_agent

    return compile_agent(self)
```
Evidence: `agentdeck/authoring/agent.py:152`, `agentdeck/authoring/compile.py:61-62`, `agentdeck/authoring/compile.py:118`, `agentdeck/authoring/workflow.py:103`, `agentdeck/deck.py:1107`

### The sync bridge blocks the event loop it detected [BAD] (severity: medium)
`_run_sync` exists because the checkpointer may be resolved from inside an async `start()`. Its answer is a thread plus `thread.join()`, which blocks the running loop for the whole database handshake: precisely the thing the standards' async section forbids. The result and error are shuttled back through single-element lists where `concurrent.futures` does the same job in one call and preserves exception plumbing for free.
```python
thread = threading.Thread(target=_runner, daemon=True)
thread.start()
thread.join()
if error:
    raise error[0]
return result[0]
```
Evidence: `agentdeck/adapters/engines/langgraph/checkpointer.py:79-105`

### The `_UNSET` override ladder is written out by hand, twice [BAD] (severity: medium)
Two declaration classes each define their own `_UNSET: Any = object()` and then hand-write the same merge: 18 `object.__setattr__` calls with string field names, each paired with a `source.x if x is _UNSET else x` ternary. Renaming a field does not move the string, the type checker cannot see any of these assignments, and the two copies have already diverged (only `Workflow` uses `getattr(source, ...)` for its required field).
```python
object.__setattr__(self, "model", source.model if model is _UNSET else model)
object.__setattr__(
    self, "model_settings", dict(source.model_settings if model_settings is _UNSET else model_settings)
)
object.__setattr__(self, "tools", tuple(source.tools if tools is _UNSET else tools))
object.__setattr__(self, "handoffs", tuple(source.handoffs if handoffs is _UNSET else handoffs))
```
Evidence: `agentdeck/authoring/agent.py:127-143`, `agentdeck/authoring/workflow.py:86-94`

### `Any` has no discipline marker, so opaque-by-contract and unannotated look identical [BAD] (severity: medium)
260 `Any` annotations sit in function signatures (167 parameters, 93 returns), 48 of them a bare `-> Any`. Some are genuinely opaque (an SDK object the library never interprets), some are simply unwritten. Nothing distinguishes them. This is the one place the project's otherwise excellent annotate-your-waivers culture is missing: every `ty: ignore` says why, and no `Any` does.
```python
def compile_hooks(hooks: Any, *, context_type: object | None = None) -> Any:
```
Evidence: `agentdeck/authoring/hooks.py:40`, `agentdeck/authoring/workflow.py` (23 sites), `agentdeck/serve.py` (19 sites)

### The mandated comment rule is false, and has been waived 1,014 times [BAD] (severity: medium)
`CLAUDE.md` says comments must be "extremely rare, max 1-2 lines". Reality: 1,014 comments (8.8 per 100 code lines), mostly 3-to-5-line blocks, plus 4,068 docstring lines, which equals 35% of the 11,557-line code count. The comments are good: I sampled every comment under 45 characters and all 68 were continuation lines of multi-line why-blocks, not a single restatement of the code. That means the implementation is right and the rule is wrong, which the standards' own final rule tells you to fix by changing the rule rather than accumulating a permanent exception.
```python
# A run can fill the queue without the loop ever turning  -  nothing on the event
# path has to suspend  -  and then a sink is "full" because the producer is fast,
# not because the sink is slow. One turn is room for a sink that is keeping up
# and no help at all to a wedged one, which is exactly the distinction wanted.
await asyncio.sleep(0)
```
Evidence: `agentdeck/runtime/dispatch.py:163-167`, `CLAUDE.md:44`

### The sqlite thread seam is copy-pasted three times, and has already drifted [BAD] (severity: medium)
Three sqlite-backed ports carry a byte-identical `_run[T]` (lock, `to_thread`, translate `sqlite3.Error` into `StoreError`), differing only in an operation-label prefix. Two carry the explanatory docstring; the lease copy has already lost it. The seam depends on nothing but stdlib plus `agentdeck.errors`, so `core/` could host it and adapters are permitted to import `core/`. The same duplication shows in `_now()`, defined identically in three adapters.
```python
async def _run[T](self, work: Callable[[], T], op: str) -> T:
    async with self._lock:
        try:
            return await asyncio.to_thread(work)
        except sqlite3.Error as exc:
            raise StoreError(f"run lease {op} failed: {exc}") from exc
```
Evidence: `agentdeck/adapters/leases/sqlite/port.py:75-80`, `agentdeck/adapters/stores/sqlite/store.py:161-168`, `agentdeck/adapters/control/sqlite/port.py:108-115`

### The `ponytail:` convention is documented nowhere, and one marker is ungreppable [BAD] (severity: medium)
The 12 markers are the project's deliberate-debt ledger, and no file in `docs/engineering/`, `CONTRIBUTING.md`, or `CLAUDE.md` mentions the word. A new contributor meeting one has no idea whether it is a rule, a joke, or a name. Worse for tooling: 11 match `# ponytail:` and the twelfth is mid-sentence inside a docstring with no `#`, so the obvious harvest command silently returns 11 of 12. A thirteenth-style problem sits in `service.py:508`, where the marker is written with a literal `#` inside a triple-quoted docstring and will render as a stray hash in any generated help text.
```python
one connection and one aiosqlite thread per loop  -  the wrong half of a trade whose right
half is a saver that works on the second loop. Zero effect on a server. ponytail: bounding
it means closing the savers at loop shutdown, i.e. owning their lifecycle  -  worth doing
when something long-lived actually runs loops in a row.
```
Evidence: `agentdeck/adapters/engines/langgraph/checkpointer.py:64`, `agentdeck/runtime/service.py:508`

### Two generic syntaxes in one package on one Python floor [BAD] (severity: low)
Six declarations use PEP 695 (`async def _run[T]`, `class Context[T]`) and four still use the pre-3.12 `TypeVar` / `Generic[T]` spelling, including two where nothing prevents the new form. The project requires Python 3.12, so this is drift, not compatibility.
```python
T = TypeVar("T")


@dataclass(slots=True)
class PluginRegistry(Generic[T]):
```
Evidence: `agentdeck/runtime/registry.py:16-23`, `agentdeck/core/context.py:120`, `agentdeck/adapters/engines/langgraph/checkpointer.py:41`
Ref: https://peps.python.org/pep-0695/

### `Final` appears on 2 of 117 module constants [BAD] (severity: low)
Both live in `runtime/discovery.py`. The other 115 module-level constants, including the mutable tables that encode lifecycle policy, are unannotated, so the type checker will not object to any of them being rebound. Either the convention is `Final` on exported constants or it is not; two out of 117 is neither.
```python
ENGINE_FOR_KIND: Final[Mapping[InvocableKind, str]] = {
    InvocableKind.AGENT: "openai-agents",
    InvocableKind.WORKFLOW: "langgraph",
}
```
Evidence: `agentdeck/runtime/discovery.py:36`, `agentdeck/runtime/discovery.py:45`

### The exemplar module is inconsistent with itself [BAD] (severity: low)
`core/status.py` is the best code in the package and still switches conventions inside 15 lines: `STATES` is annotated `Mapping` and documented with a trailing attribute docstring (a Sphinx and IDE convention, not a language feature), then `TRANSITIONS` is annotated as a mutable `dict` and documented with a `#` comment above it. Both are exported policy tables; both should be described and typed the same way.
```python
STATES: Mapping[RunStatus, StateFacts] = {...}
"""Which state a suspension gets is decided by how it resumes, not by who caused it..."""

# Only these kinds move the needle; everything else (deltas, tool calls, node.updated, ...)
# leaves status exactly where it was.
TRANSITIONS: dict[str, RunStatus] = {
```
Evidence: `agentdeck/core/status.py:71-85`

### `build_asgi_app` is a 198-line closure holding 18 handlers [BAD] (severity: low)
It is the idiomatic FastAPI factory shape and the only function in the package over 100 lines, so this is a shape complaint, not a complexity one: nesting stays shallow and no handler exceeds 31 lines. The cost is real anyway. Eighteen route handlers, five exception handlers, and three private helpers exist only as closure locals, so none can be imported, unit-tested, or reused without constructing the whole app, and the module reads as one function where every peer module reads as a class.
```python
def build_asgi_app(deck: Deck) -> Any:
```
Evidence: `agentdeck/serve.py:101-298`

### 26 warnings, 1 debug: nothing to turn on when a run goes quiet [BAD] (severity: low)
Of 53 log calls, 26 are `warning`, 11 `exception`, 10 `info`, 5 `error`, and 1 `debug`. The library is loud about things an operator cannot act on and silent about the run lifecycle, so raising the log level during a stall reveals nothing about claims, leases, safe points, or engine hand-off. Several `ponytail:` markers say "log it if an operator ever has to find out why", which is the same gap seen from the other side.
```python
logger.debug("agent node %s: start", self.agent.name)  # the only logger.debug in the package
```
Evidence: `agentdeck/authoring/nodes.py:111`, `agentdeck/adapters/stores/sqlite/store.py:132`, `agentdeck/adapters/control/sqlite/port.py:82`

## Bottom line

The craft here is well above the norm for an SDK this young: table-driven policy, one async-generator idiom, annotated suppressions, uniform error-text shapes, and a `ponytail:` convention where every deliberate shortcut names its own ceiling and upgrade trigger. Four of those are worth stealing outright: the single frozen schema base with its policy reason attached, `Available: {sorted(...)}` on every not-found error, the reason-on-the-same-line rule for lint and type waivers, and the debt-marker format that is useless unless it states a trigger. The anti-patterns are all duplication rather than confusion: a triplicated thread seam, a hand-written sentinel ladder, an authoring-ring cycle held together by function-local imports, and `Any` used as both a contract and a shrug with no way to tell which. The one rule genuinely worth rewriting is the comment mandate, which the code has correctly ignored a thousand times.
