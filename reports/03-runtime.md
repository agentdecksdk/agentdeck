# Runtime audit: `agentdeck/runtime/` and the core lifecycle machinery

The runtime is the strongest part of this SDK. Its two central rulings, that the store assigns
`seq`/`ts` in the write that persists the event and that every state transition is a conditional
append rather than a check followed by a write, close the entire family of races that usually
makes an orchestrator unfixable. What is left is a set of real but bounded problems: two
non-atomic write sequences, a port contract that understates its own condition, and a control
plane that stops at one machine.

## Findings

### The store assigns seq and ts in the write that persists the event [GOOD] (severity: high)
Nothing in the runtime holds a counter, so a number cannot be allocated without being persisted.
That is what makes `seq` dense and the "spot a gap, refetch it" promise actually converge.
```python
event = (await self._store.append(ctx.log_key, [payload], ctx, spec.name))[0]
await self._fan_out(event)
return event
```
Evidence: `agentdeck/runtime/service.py:875`

### Persist, fan out, yield, in that order, on every path [GOOD] (severity: high)
An event a consumer has seen is already durable. There is exactly one write helper and every
lifecycle path funnels through it, so the ordering cannot be honored in one branch and lost in
another.
```python
yield await self._record(payload, spec, ctx)
```
Evidence: `agentdeck/runtime/service.py:441`, `agentdeck/runtime/service.py:876`

### The lifecycle state machine is a total table that fails at import [GOOD] (severity: high)
Four declarations, keyed off the enum members rather than off the rows, so a status added without
a row raises `KeyError` while the module is importing instead of answering wrongly at 3am. No
invalid transition is expressible because no branch decides one.
```python
PRECONDITIONS = {(s, o): _LEGALITY[s][o] for s in RunStatus for o in Operation}
POLICY = {(s, v): _ROUTING[s][v] for s in RunStatus for v in _RUNNING_ROW}
```
Evidence: `agentdeck/core/status.py:165`, `agentdeck/core/status.py:260`

### Terminal and suspended kinds are derived from one table, not listed twice [GOOD] (severity: medium)
`TERMINAL_STATUSES` and `SUSPENDED_KINDS` are comprehensions over `TRANSITIONS`/`STATES`, so the
kind side and the status side of "suspended" cannot drift. This replaced two hand-written
frozensets in two modules.
```python
TERMINAL_STATUSES = frozenset(TRANSITIONS[kind] for kind in TERMINAL_KINDS)
SUSPENDED_KINDS = frozenset(k for k, s in TRANSITIONS.items() if STATES[s].suspended)
```
Evidence: `agentdeck/core/status.py:101`

### One conditional append is the only arbiter of both claims [GOOD] (severity: high)
Session start and suspended-to-running both commit through a store operation that tests and
writes indivisibly, and all three shipped stores hold that with a real lock (SQLite
`BEGIN IMMEDIATE`, Postgres advisory lock). A loser is told, never interleaved. This holds across
processes, which a Python-side pre-check never could.
```python
claim, event = await self._store.claim_start(
    ctx.log_key, opening, ctx, spec.name, self._stale_run_after, dead=await self._dead_runs(history)
)
if claim.held_by is not None or event is None:
    raise SessionBusyError(await self._session_busy_message(ctx, claim.held_by))
```
Evidence: `agentdeck/runtime/service.py:615`, `agentdeck/adapters/stores/sqlite/store.py:396`

### The resume claim carries the answer, not just the fact of one [GOOD] (severity: high)
Writing the value in the same append that flips the status removes the window in which the log
says a run was answered but no longer holds what the answer was, which is unrecoverable because
the engine is still parked at its interrupt.
```python
resumed = RunResumed(reason=reason, value=_as_content(value, ctx.run_id))
event = await self._store.claim_resume(ctx.log_key, ctx.run_id, resumed, ctx, spec.name)
```
Evidence: `agentdeck/runtime/service.py:723`

### CancelledError is handled separately from Exception, and the closing write is shielded [GOOD] (severity: high)
`CancelledError` is a `BaseException`, so the generic arm never saw it and runs stayed open in the
log forever. Both arms now exist, and the closing append is shielded because an unshielded await
inside a cancelled task is re-cancelled before the write lands.
```python
recording = asyncio.ensure_future(self._record(RunCancelled(reason=reason), spec, ctx))
with suppress(asyncio.CancelledError):
    await asyncio.shield(recording)
```
Evidence: `agentdeck/runtime/service.py:454`, `agentdeck/runtime/service.py:697`

### A cancellation landing inside the opening claim has its own arm [GOOD] (severity: medium)
The claim commits the run before anything is yielded, and it is awaited in the coroutine an ASGI
server cancels on early client disconnect. Without this arm the run stays open and holds its
session for a whole staleness window.
```python
except asyncio.CancelledError:
    if await self._store.run_status(ctx.log_key, ctx.run_id, ctx) is not None:
        await self._close_cancelled(spec, ctx, "cancelled during the claim")
    raise
```
Evidence: `agentdeck/runtime/service.py:196`

### An engine that just stops is closed for it [GOOD] (severity: medium)
A stream ending on neither a terminal nor a suspended kind leaves consumers waiting forever. The
runtime records `run.failed` and names the engine rather than trusting the adapter.
```python
if last not in TERMINAL_KINDS and last not in SUSPENDED_KINDS:
    yield await self._record(_engine_failed(...), spec, ctx)
```
Evidence: `agentdeck/runtime/service.py:469`

### Terminal means terminal inside the run loop [GOOD] (severity: medium)
Reading stops at the first terminal payload, so an engine that yields more after it gets it
discarded rather than appended behind the log's last word.
```python
last = payload.kind
if last in TERMINAL_KINDS:
    break
```
Evidence: `agentdeck/runtime/service.py:442`

### The runtime is the only thing in the tree that appends [GOOD] (severity: medium)
Two call sites, both in `service.py`. No engine adapter, surface, or telemetry sink can write to
the log, which is what makes the single-writer invariants above worth anything.
```python
# the only two `store.append` call sites in agentdeck/
event = (await self._store.append(ctx.log_key, [payload], abandoned, tail.origin))[0]  # :679
event = (await self._store.append(ctx.log_key, [payload], ctx, spec.name))[0]          # :882
```
Evidence: `agentdeck/runtime/service.py:679`, `agentdeck/runtime/service.py:882`

### Sink fan-out is bounded, drops the stalest, and never waits on a sink [GOOD] (severity: high)
One queue and one consumer per sink. A full queue costs one loop turn, which distinguishes a fast
producer from a wedged sink, and then drops the oldest event so the newest (including the terminal
one) survives. A run is never charged for its slowest reader.
```python
except asyncio.QueueFull:
    await asyncio.sleep(0)
while True:
    try:
        self._queue.put_nowait(event)
        return
    except asyncio.QueueFull:
        self._count_drop(self._queue.get_nowait())
        self._queue.task_done()
```
Evidence: `agentdeck/runtime/dispatch.py:148`

### Every shutdown wait in the dispatch has a finite deadline [GOOD] (severity: medium)
Five separate budgets (emit, flush, close, reap, breaker cooldown), and the consumer is reaped
with `asyncio.wait` from the outside rather than a timeout wrapped around `await consumer`, because
a deadline fires by cancelling the waiting task and a task suspended on another hands that cancel
straight into the sink that just proved it swallows them.
```python
reaped, _ = await asyncio.wait({consumer}, timeout=REAP_TIMEOUT)
```
Evidence: `agentdeck/runtime/dispatch.py:245`

### The dispatch tells its own cancellation apart from a swallowed one [GOOD] (severity: medium)
`task.cancelling()` is the check, at every point a `CancelledError` is caught. Swallowing a cancel
aimed at this task would hand the caller a clean return from something it asked to stop.
```python
def _cancelling_ourselves() -> bool:
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0
```
Evidence: `agentdeck/runtime/dispatch.py:79`

### A dead consumer task is restarted instead of silently swallowing the stream [GOOD] (severity: medium)
A `CancelledError` escaping a sink's own emit kills the consumer without raising anything at the
producer, after which a queue nobody reads eats every event. `submit` refuses first once closed, so
the restart cannot resurrect a queue that `close` already counted as lost.
```python
if self._consumer is None or self._consumer.done():
    self._consumer = asyncio.create_task(self._consume())
```
Evidence: `agentdeck/runtime/dispatch.py:303`

### Zero capacity is rejected rather than clamped [GOOD] (severity: low)
`asyncio.Queue(0)` is unbounded, the exact opposite of what a caller asking for zero meant. All
three constructor knobs are validated.
```python
if capacity <= 0:
    raise ValueError(f"capacity must be greater than 0, got {capacity}")
```
Evidence: `agentdeck/runtime/dispatch.py:116`

### Lease ignorance never reads as death [GOOD] (severity: high)
The port verb is `dead()`, not `alive()`, and it answers with positive knowledge only. Inverting it
would make every worker take over every other worker's runs on sight. No backend, no knowledge of
these runs, and an unreachable backend all collapse to the empty set.
```python
if self._lease is None or not history:
    return frozenset()
try:
    return await self._lease.dead({event.run_id for event in history})
except StoreError:
    return frozenset()
```
Evidence: `agentdeck/core/ports/lease.py:56`, `agentdeck/runtime/service.py:532`

### A lease backend cannot fail a working turn [GOOD] (severity: medium)
Acquire, renew, and release are all wrapped: a lease is an improvement on the staleness timer, so
an unreachable backend leaves the run playing under the timer alone. The renewer keeps trying
after a failure rather than giving up for the rest of the run.
```python
with suppress(StoreError):
    if not await lease.acquire(run_id, self._lease_ttl):
        logger.warning("run %s is already leased by another worker", run_id)
```
Evidence: `agentdeck/runtime/service.py:491`, `agentdeck/runtime/service.py:520`

### Suspension is checked before both liveness mechanisms [GOOD] (severity: high)
A parked run has no worker to be dead and no reason to be writing, so neither the timer nor an
expired lease applies to it. Getting this backwards is how a platform silently destroys a pending
human approval to free a session lock.
```python
if STATES[status].suspended:
    return SessionClaim(held_by=events[-1].run_id), None
```
Evidence: `agentdeck/adapters/stores/memory/store.py:133`, `agentdeck/core/ports/store.py:143`

### The report buffer is per run, bounded, and returned rather than stored [GOOD] (severity: medium)
Two concurrent runs on one Runtime cannot drain into each other, and an invocable's own code
cannot grow the buffer past 64. The drop is newest-first so a progress sequence does not appear to
start at step 40.
```python
reports: deque[KnownPayload] = deque()
return replace(ctx, gate=gate, reporter=Reporter(reports)), reports
```
Evidence: `agentdeck/runtime/service.py:833`, `agentdeck/core/reporting.py:67`

### The report drain takes its count once, so an emitter cannot starve the engine [GOOD] (severity: low)
A report arriving while these are being written belongs to the next batch. Drained before each
payload, never after, so nothing follows a terminal event into the log.
```python
for _ in range(len(reports)):
    payload = reports.popleft()
```
Evidence: `agentdeck/runtime/service.py:859`

### A refused advisory append costs the report, not the run [GOOD] (severity: medium)
A store that dislikes one kind would otherwise turn a run that would have completed into
`run.failed`. The refused append never took a number, so the log stays dense.
```python
except StoreError:
    logger.warning("run %s could not record its %s; dropping the report", ctx.run_id, payload.kind)
```
Evidence: `agentdeck/runtime/service.py:863`

### The run id is minted, never derived from the caller's key [GOOD] (severity: medium)
Two separate context factories exist precisely so no fallback can let a caller-supplied value
reach `run_id`. Two namespaces given one key still get two unrelated runs.
```python
return RunContext(run_id=str(uuid4()), key=key, session_id=session_id, ...)
```
Evidence: `agentdeck/runtime/service.py:823`

### An unknown event kind degrades instead of taking a session's log with it [GOOD] (severity: medium)
Stores parse every row, so one unrecognised event would fail a whole read. It lands as
`UnknownEvent` unless the envelope and payload copies of `kind` disagree, which is the one case
that genuinely means the row is not what it claims.
```python
try:
    return handler(data)
except ValidationError:
    if not isinstance(data, dict):
        raise
```
Evidence: `agentdeck/core/events.py:455`

### Crash recovery is tested with real SIGKILL across OS processes [GOOD] (severity: high)
Not mocked, not simulated. A separate worker process is killed mid-run and the surviving process
is asserted against the log, plus multiprocess races for double resume, cancel versus completion,
concurrent session start, and seq continuity after restart.
```python
victim.kill()
assert victim.returncode == -signal.SIGKILL, f"the victim was not killed mid-..."
```
Evidence: `tests/test_crash_reconciliation.py:378`, `tests/test_multiprocess_concurrency.py:405`

### Deck-owned run tasks are strongly referenced and reaped [GOOD] (severity: medium)
Every background execution is held in a dict, has a done callback that retrieves its exception so
asyncio does not log it as unretrieved, and is cancelled and awaited before the runtime drains.
No fire-and-forget task in the tree lacks an owner.
```python
task = asyncio.create_task(_drain(agen))
self._executions[opening.run_id] = task
task.add_done_callback(functools.partial(self._execution_done, opening.run_id))
```
Evidence: `agentdeck/deck.py:824`, `agentdeck/deck.py:746`

### A store fault between the claim and the cancel leaves a run RUNNING with no executor [BAD] (severity: medium)
Cancelling a suspended run is three writes, and only the first is atomic. A `StoreError` on either
`_record` leaves the run flipped to `RUNNING` with nothing playing it, no terminal event, and
`signal()` raising instead of falling through to record the intent, so nothing ever retries. The
run then holds its session for a full `stale_run_after` (one hour by default) rather than parking
indefinitely as it was. The same shape appears in both TERMINATE arms of `resume`/`resume_run`.
```python
if await self._claim_resume(spec, run_ctx, None, reason) is None:
    return False
await self._record(ControlRequested(verb="cancel", reason=reason), spec, run_ctx)
await self._record(RunCancelled(reason=reason), spec, run_ctx)
return True
```
Evidence: `agentdeck/runtime/service.py:404`, `agentdeck/runtime/service.py:267`, `agentdeck/runtime/service.py:333`

### The `claim_resume` port contract understates its own condition [BAD] (severity: medium)
The docstring says the append happens if and only if the run is `WAITING_ANSWER`. All three
shipped stores use `can_resume`, which also accepts `PAUSED`, and `resume_run` depends on that. A
third-party store author implementing the contract as written silently breaks pause lifting, and
the failure mode is a no-op return rather than an error.
```python
"""Stamp and append ``resumed`` if and only if ``run_id`` is ``WAITING_ANSWER``, in one
# sqlite/store.py:401, postgres/store.py:387, memory/store.py:162 all do:
if not can_resume(status_of(...)):
    return None
```
Evidence: `agentdeck/core/ports/store.py:165`, `agentdeck/adapters/stores/sqlite/store.py:401`

### Cancel and pause do not cross processes in the shipped default [BAD] (severity: medium)
`AGENTDECK_CONTROL` defaults to `memory://`, so a signal written in one process is invisible to
every other. `signal()` still returns `True`, because a port exists and the write landed: the
caller gets success for a cancel the target run can never see. The `False` return, documented as
the one answer a caller has to act on, only fires when no port is wired at all, which the default
resolution never produces.
```python
if self._control is None:
    return False
await self._control.signal(id, verb, reason)
return True
```
Evidence: `agentdeck/runtime/service.py:376`, `agentdeck/composition.py:199`

### No control or lease backend exists for a multi-machine deployment [BAD] (severity: medium)
Event stores ship memory, sqlite, postgres, and redis. Control and lease ship memory and sqlite
only, and SQLite behind more than one machine is unsupported by its own docstring. So the exact
deployment the Postgres store exists for, several nodes on one database, has no cross-node cancel
and no cross-node liveness: crash recovery degrades to the one-hour timer and cancellation
degrades to process-local.
```python
# adapters/stores/: memory, postgres, redis, sqlite
# adapters/control/: memory, sqlite
# adapters/leases/:  memory, sqlite
```
Evidence: `agentdeck/runtime/settings.py:487`, `agentdeck/composition.py:185`

### A blocked event loop lets a live run's lease lapse [BAD] (severity: medium)
The renewer is an asyncio task, so a tool doing synchronous I/O or CPU-bound work for longer than
one TTL (90s default) cannot renew. A peer then reads the run as dead, takes the session, and
writes `run.failed` for a run that is still working, which is one turn per session no longer
holding. Known and documented; the mitigation on offer is raising the TTL above the longest stall
a deployment can produce, which is not a property the code can check.
```python
while True:
    await asyncio.sleep(interval)
    try:
        if not await lease.renew(run_id, self._lease_ttl):
            logger.warning("run %s lost its lease while still playing", run_id)
```
Evidence: `agentdeck/runtime/service.py:508`

### A resume can answer an interrupt other than the one in flight [BAD] (severity: medium)
Nothing in the claim names which interrupt is being answered, so a stale answer from a client that
was looking at an earlier question is accepted as the current one. The status check cannot catch
it: both are `WAITING_ANSWER`. Acknowledged in the port as deferred to a schema change (#94).
```python
resumed = RunResumed(reason=reason, value=_as_content(value, ctx.run_id))
event = await self._store.claim_resume(ctx.log_key, ctx.run_id, resumed, ctx, spec.name)
```
Evidence: `agentdeck/core/ports/store.py:174`, `agentdeck/runtime/service.py:723`

### The staleness window's documented skew hazard no longer matches the code [BAD] (severity: medium)
The setting's docstring tells operators that each worker compares its own clock against timestamps
its peers stamped, and to treat the window as a budget skew eats into. It does not: the cutoff is
computed inside the store from the store's own clock, so N workers on one Postgres compare one
`clock_timestamp()`. The advice sends operators tuning a real timeout after a hazard that was
designed out.
```python
stale_before = (await cursor.fetchone())[0] - stale_after
if last.run_id not in dead and last.ts > stale_before:
```
Evidence: `agentdeck/runtime/settings.py:329`, `agentdeck/adapters/stores/postgres/store.py:334`

### Every turn reads the session's whole history before it can start [BAD] (severity: medium)
One unbounded read per turn, and the result is also what feeds the engine and the lease question.
Cost grows linearly with conversation length, on the latency path of every single turn. Flagged in
place as deferred.
```python
history = await self._store.read(ctx.log_key, ctx)
```
Evidence: `agentdeck/runtime/service.py:186`

### `pending()` reads every parked run's whole log, and an inbox polls it [BAD] (severity: medium)
Cost is (parked runs x their length) per call, on a path a UI hits on a timer, to recover two
fields from the last interrupt. The listing and the reads are also two snapshots, which is benign
here only because the resume claim rechecks status.
```python
for summary in await self._store.list_runs(ctx, status=RunStatus.WAITING_ANSWER):
    found = _last_interrupt(await self._store.read_run(summary.log_key, summary.run_id, ctx))
```
Evidence: `agentdeck/runtime/service.py:749`

### The Gate ignores a lost compare-and-set [BAD] (severity: low)
`consume` is a compare-and-set and its result is discarded. `_route` handles the same lost race by
re-reading the port and ruling again; the gate instead halts the run on an intent that changed
under it. Narrow window, but the two readers of the same port disagree on what a lost set means.
```python
if ruling.consume:
    await self._control.consume(self._id, pending.verb)
raise _HALTED_BY[pending.verb](self._id, safe_point, pending.reason)
```
Evidence: `agentdeck/core/control.py:159`, `agentdeck/runtime/service.py:585`

### A store blip at the terminal moment loses the closing event with no retry [BAD] (severity: low)
If the append of `run.failed` raises, the original engine exception is replaced by the store error
and no terminal event lands, so the run stays open for a staleness window. Unavoidable when the
store is genuinely down (there is nowhere to write the record), and the timer is the designed
recovery, but a transient blip gets no second attempt at a write that would have succeeded.
```python
except Exception as exc:
    logger.exception("run %s failed in engine %r", ctx.run_id, engine.engine)
    yield await self._record(_failed(exc, engine.engine), spec, ctx)
    raise
```
Evidence: `agentdeck/runtime/service.py:462`

### `append` has no terminal guard [BAD] (severity: low)
"Terminal is terminal" is enforced by the run loop's `break` and by the runtime being the only
appender, not by the persistence contract. No shipped path can violate it, but the invariant most
of the reader side depends on has no backstop in the store, so the next component that gains write
access inherits the obligation silently.
```python
async def append(self, log_key: str, payloads: Sequence[KnownPayload], ctx: RunContext, origin: str)
```
Evidence: `agentdeck/core/ports/store.py:69`

### `runtime/capture.py` is dead, and the CHANGELOG says otherwise [BAD] (severity: low)
Already on the register as dead (verified 2026-08-11, a cleanup issue offered and declined), so the
32 dead lines are a known accepted cost. The new part: the CHANGELOG entry that kept them says the
tracer still uses them, and it does not. `Capture` and `CaptureActor` have zero references across
`agentdeck/`, `tests/`, and `examples/`, so the stated reason for retaining the module is false.
```python
class Capture(BaseModel):
    session_id: str | None = None
```
Evidence: `agentdeck/runtime/capture.py:25`, `docs/delivery/findings-register.md:147`, `CHANGELOG.md:862`

### `control_poll_interval` is the one Runtime knob with no setting behind it [BAD] (severity: low)
Documented as a peer of the five arguments `build_runtime` resolves, but `build_runtime` never
passes it, so the 0.2s constant is the only value any deployment gets. The latency-versus-read-rate
trade the docstring describes is not actually available to an operator.
```python
return Runtime(engines, store, specs, sinks=sinks, control=control,
               stale_run_after=stale_run_after, lease=resolve_lease_port(), ...)
```
Evidence: `agentdeck/composition.py:88`, `agentdeck/runtime/service.py:126`

### Duplicate engine names collapse silently [BAD] (severity: low)
Two engines claiming one name leave the last one registered with no warning, and the runtime then
plays every spec for that name on an instance the caller may not have intended. Every other
name collision in this tree (two bundles, an agent versus a workflow, a reused key) raises.
```python
self._engines = {engine.engine: engine for engine in engines}
```
Evidence: `agentdeck/runtime/service.py:130`

### `mount_project_dir` mutates `sys.modules` process-wide [BAD] (severity: low)
One alias slot, and every previously cached submodule is deleted on each mount. Correct for the
stated one-project-per-process rule, but it is a process-global side effect reachable from a
discovery scan, so two Decks built concurrently in one process silently invalidate each other's
bundles.
```python
for cached in [name for name in sys.modules if name.startswith(f"{_PROJECT_ALIAS}.")]:
    del sys.modules[cached]
sys.modules[_PROJECT_ALIAS] = module
```
Evidence: `agentdeck/runtime/registry.py:156`

### `key` is a uniqueness constraint, not an idempotency token [BAD] (severity: low)
A retried request with the same key raises `DuplicateKeyError` instead of returning the run that
holds it. That is a deliberate deterministic-failure ruling, but it means idempotent retry is not
a runtime feature: every caller has to catch the error and do its own `find_by_key` lookup, and
nothing in the runtime offers the combined operation.
```python
raise DuplicateKeyError(f"key {ctx.key!r} is already used by run {holder!r} in namespace ...")
```
Evidence: `agentdeck/core/ports/store.py:151`, `agentdeck/deck.py:1376`

### `PluginRegistry.list()` hands out its live cache with read-only enforced by comment [BAD] (severity: low)
"Treat it as read-only; mutations corrupt subsequent lookups" is a docstring, not a guarantee, and
the caller is external. A `MappingProxyType` or a copy would cost nothing here.
```python
if refresh or self._cache is None:
    self._cache = self._scan()
return self._cache
```
Evidence: `agentdeck/runtime/registry.py:54`

## Bottom line

The hard parts are right: the lifecycle is a total table that cannot express an invalid
transition, every state change commits through a conditional append that holds across processes,
cancellation is handled at both the generator and task level with the closing write shielded, and
none of it is trusted on faith (real SIGKILL crash tests, real multiprocess races). The two
mechanical defects worth fixing are the non-atomic terminate sequence and the `claim_resume`
contract that understates its own condition. The real ceiling is not code quality but reach: with
control and leases stopping at SQLite, the runtime's cancellation and crash-recovery guarantees do
not extend to the multi-node deployment its own Postgres store points at, and the default
configuration reports a cancel as delivered when a second worker can never see it.
