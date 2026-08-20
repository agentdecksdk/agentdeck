# Adapters Audit

4,947 LOC across six adapter families: engines (`openai_agents`, `langgraph`, `stub`), event stores (`memory`, `sqlite`, `redis`, `postgres`), the MCP tool source, Langfuse telemetry, and control/lease ports (`memory`, `sqlite` only). There is no `caps` package under `adapters/`; the scope note that named one does not match the tree. The layer is the most carefully reasoned code in the repo (the concurrency comments are load-bearing, not decoration) and it is also where the product's scaling story breaks: the event log reaches four backends and everything beside it stops at two.

## Findings

### No adapter imports another adapter [GOOD] (severity: high)
Grepped every `from agentdeck.` in `adapters/`: each package imports only `agentdeck.core`, `agentdeck.errors`, and its own submodules. The "delete any adapter directory and nothing outside it breaks" claim holds as written.
```python
# adapters/stores/sqlite/store.py:22
from agentdeck.errors import DuplicateKeyError, StoreError
# adapters/tools/mcp/lifecycle.py:23-24
from agentdeck.adapters.tools.mcp.transport import MCPServerStreamableHttpResilient
from agentdeck.errors import ConfigError
```
Evidence: `agentdeck/adapters/stores/sqlite/store.py:22`

### One import-linter contract per technology, with two holes [GOOD] (severity: medium)
Nine contracts pin `agents`, `langgraph`, `redis`, `psycopg`, `mcp`, and `langfuse` each to exactly one adapter, and the comments explain why import-linter alone cannot express `agents.mcp` (ruff TID251 covers it). The holes: `adapters.engines.langgraph` and `adapters.tools.mcp` appear in no purity contract as a *source*, so either could import `agentdeck.runtime` or `agentdeck.deck` and no gate would notice.
```python
[importlinter:contract:pure-adapters-stay-pure]
source_modules =
    agentdeck.adapters.stores.memory
    agentdeck.adapters.stores.sqlite
    agentdeck.adapters.engines.stub
    agentdeck.adapters.control.memory
```
Evidence: `.importlinter:69-79`

### SQLite writes take the lock before the read [GOOD] (severity: high)
Every append and both claims open `BEGIN IMMEDIATE`, because `MAX(seq)`-then-`INSERT` inside a deferred transaction upgrades read to write and SQLite answers `SQLITE_BUSY_SNAPSHOT` without honoring `busy_timeout`. Correct, and the comment names the failure mode rather than the fix.
```python
self._conn.execute("BEGIN IMMEDIATE")
with self._conn:
    return self._stamp_and_insert(log_key, payloads, ctx, origin)
```
Evidence: `agentdeck/adapters/stores/sqlite/store.py:261-263`
Ref: https://www.sqlite.org/lang_transaction.html

### Postgres claims: per-log advisory lock plus a pinned isolation level [GOOD] (severity: high)
Writes take a transaction-scoped `pg_advisory_xact_lock` on `(namespace, log_key)` with `lock_timeout` set first, and the connection pins `READ COMMITTED` so a server configured for `REPEATABLE READ` cannot silently break the claim (the loser must see the winner's committed rows after the lock is handed over). The lock key is a blake2b digest rather than Postgres's undocumented `hashtext`.
```python
await conn.execute("SELECT set_config('lock_timeout', %s, true)", (f"{_LOCK_TIMEOUT_MS}ms",))
await conn.execute("SELECT pg_advisory_xact_lock(%s)", (_advisory_key(f"{namespace}\x00{log_key}"),))
```
Evidence: `agentdeck/adapters/stores/postgres/store.py:432-433`

### Postgres reconnect keys on the right property [GOOD] (severity: medium)
`_run` drops a dead connection on `psycopg.Error` so the next call redials. Verified against psycopg 3.3.4: `closed` is `pgconn.status == BAD`, and `broken` is documented as a subset of `closed`, so this catches a server-side disconnect and not only an explicit `close()`.
```python
except psycopg.Error as exc:
    if self._conn is not None and self._conn.closed:
        self._conn = None
    raise StoreError(f"event log {op} failed: {exc}") from exc
```
Evidence: `agentdeck/adapters/stores/postgres/store.py:190-195`

### SQL is composed, never interpolated [GOOD] (severity: high)
The caller-supplied schema name goes through `psycopg.sql.Identifier` for every statement, composed once in `__init__`. No f-string SQL anywhere in the postgres adapter. The SQLite adapter parameterizes every value and builds its only dynamic fragment from a literal kind tuple.
```python
table = sql.Identifier(schema, "events")
self._insert = sql.SQL(
    "INSERT INTO {table} (namespace, log_key, run_id, key, seq, data) VALUES (%s, %s, %s, %s, %s, %s::jsonb)"
).format(table=table)
```
Evidence: `agentdeck/adapters/stores/postgres/store.py:100-149`

### Redis key segments are escaped [GOOD] (severity: high)
Every namespace, log key, and run id is percent-encoded before it becomes a key segment, so namespace `a:b` + log `c` cannot collide with namespace `a` + log `b:c`. The docstring states the exact isolation failure it prevents. `_member`/`_split` round-trip through the same escaping.
```python
def _segment(value: str) -> str:
    return quote(value, safe="")
```
Evidence: `agentdeck/adapters/stores/redis/store.py:72-78`

### Checkpoint savers are cached per event loop, not per process [GOOD] (severity: medium)
The async sqlite and postgres savers hold asyncio primitives that bind to the first loop to contend for them, so a process-wide cache hands a second loop a saver the first one owns. `_per_loop` keys a `WeakKeyDictionary` on the running loop and deliberately caches nothing when no loop is running.
```python
try:
    loop = asyncio.get_running_loop()
except RuntimeError:
    return build()
per_loop = _savers.setdefault(loop, {})
```
Evidence: `agentdeck/adapters/engines/langgraph/checkpointer.py:68-76`

### The interrupt drain is ordered the only safe way [GOOD] (severity: high)
When a branch calls `interrupt()`, the engine drains `astream` to completion before emitting the pause, because returning early leaves an async checkpointer's write unflushed: the resume then finds the pre-interrupt checkpoint, re-runs the node, and interrupts forever. Sibling-branch reports found during the drain are forwarded; the pause stays last so a client can treat it as "nothing more this call".
```python
async for payload in self._drain_after_interrupt(stream):
    yield payload
yield pause
return  # the graph suspended; its terminal event arrives on resume
```
Evidence: `agentdeck/adapters/engines/langgraph/engine.py:350-353`

### Error text is secret-aware [GOOD] (severity: medium)
The sqlite checkpointer names the file path in its `StoreError` (an operator needs it); the postgres branch deliberately omits the DSN because it can carry a password, matching how both networked event stores word theirs. This is the kind of detail most codebases get wrong in exactly one of the two branches.
```python
except psycopg.Error as exc:
    raise StoreError(f"cannot open the workflow checkpoint (AGENTDECK_CHECKPOINT): {exc}") from exc
```
Evidence: `agentdeck/adapters/engines/langgraph/checkpointer.py:196-201`

### Optional extras stay optional [GOOD] (severity: medium)
Every optional dependency is either function-local at its import site or eager inside a submodule that the composition root imports lazily. `import agentdeck` and `Deck.run()` pull no `fastapi`, `redis`, `psycopg`, or `langfuse`. Each guarded import raises with the extra named and a docs link.
```python
except ImportError as exc:
    raise ImportError(
        f"checkpoint backend 'postgres' needs langgraph-checkpoint-postgres  -  {_DURABILITY_HINT}",
    ) from exc
```
Evidence: `agentdeck/adapters/engines/langgraph/checkpointer.py:184-190`

### The control-port migration refuses to guess [GOOD] (severity: medium)
A pre-namespace `signals` table keyed by bare `run_id` cannot be attributed to a namespace, so an empty one is dropped and a non-empty one raises with instructions. The alternative (re-key by identity) would deliver a stale signal to the wrong run, which is the defect the schema change exists to fix.
```python
if pending:
    raise StoreError(
        f"{db_path!r} has {pending} pending control signal(s) recorded under the pre-namespace "
        "schema (run_id only). There is no way to tell which were ever signaled through a "
```
Evidence: `agentdeck/adapters/control/sqlite/port.py:56-63`

### All four stores implement the whole port [GOOD] (severity: medium)
`append`, `read`, `read_run`, `claim_start`, `claim_resume`, `list_runs`, `find_by_key` are real implementations in memory, sqlite, redis, and postgres. No `NotImplementedError`, no "unsupported on this backend" branch, and the contract suite parametrizes the same cases over all of them.
```python
async def claim_start(
    self,
    log_key: str,
    opening: RunStarted,
    ctx: RunContext,
    origin: str,
    stale_after: timedelta,
    *,
    dead: frozenset[str] = frozenset(),
) -> tuple[SessionClaim, Event | None]:
```
Evidence: `agentdeck/adapters/stores/redis/store.py:225-234`

### `telemetry/langfuse/client.py` is covered, contra the tests scout [GOOD] (severity: low)
Re-verified: `build_client` is exercised against the real SDK in `tests/test_langfuse_tracer.py:216-222` (bounded exporter, `localhost:1` base URL) and against a `sys.modules` fake in `tests/test_observability.py:636-657`, which asserts the env vars it sets. The scout read "stubbed at its one construction seam" off a docstring and concluded the module is untested. The genuine telemetry gap is elsewhere: nothing drives `LangfuseTracer.root` or `_LangfuseObservation.child/finish` against the real SDK.
```python
def test_build_client_yields_a_sink_over_the_real_sdk(spy) -> None:
    client = build_client(
        LangfuseSettings(public_key="pk-lf-configured", secret_key="sk-lf-test", base_url="http://localhost:1")
    )
```
Evidence: `tests/test_langfuse_tracer.py:216-220`

### Control and lease ports stop at memory and sqlite [BAD] (severity: high)
Event stores reach redis and postgres; control signals and run leases do not. A multi-worker deployment on `AGENTDECK_EVENTS=postgresql://` has to point `AGENTDECK_CONTROL` at a shared sqlite file, and that store's own docstring says WAL is unreliable on NFS/SMB. So a containerized multi-node deployment has no working cancel path and no cross-worker lease: `memory://` logs a warning and silently degrades to per-process.
```python
if scheme == "memory":
    logger.warning(
        "AGENTDECK_CONTROL is 'memory://': a signal written in one process is invisible to another"
    )
    return MemoryControlPort()
if scheme == "sqlite":
    ...
raise ValueError(f"unknown control backend {scheme!r} ...; expected memory or sqlite")
```
Evidence: `agentdeck/composition.py:168-182`

### The openai-agents session key is not escaped [BAD] (severity: high)
`ExecutionStore.session_for` concatenates namespace and log key with a bare colon, so namespace `a:b` + session `c` and namespace `a` + session `b:c` resolve to the same SDK conversation. That is cross-namespace history bleed in the engine's own memory. The same repo's Redis event store escapes exactly this with `_segment()` (store.py:72-78), and this docstring even cites the stores' namespace isolation as the reason the prefix exists.
```python
def session_for(self, ctx: RunContext) -> Session:
    key = f"{ctx.namespace_key}:{ctx.log_key}"
    if self._session_factory is not None:
        return self._session_factory.session_for(key)
    return self._local.setdefault(key, SQLiteSession(key))
```
Evidence: `agentdeck/adapters/engines/openai_agents/sessions.py:100-104`

### LangGraph runs report zero token usage, always [BAD] (severity: high)
The langgraph engine's terminal payload hardcodes `Usage(input_tokens=0, output_tokens=0)` and it emits no `usage.reported` anywhere. Every workflow run is therefore cost-invisible: the Langfuse sink's `usage_details`/`cost_details` get zeros, and no dashboard can attribute spend to a workflow. The openai-agents engine emits one `usage.reported` per finished model call for the same information.
```python
yield RunCompleted(
    output=[DataBlock(data=self._as_data(state, "final state"))],
    usage=Usage(input_tokens=0, output_tokens=0),
)
```
Evidence: `agentdeck/adapters/engines/langgraph/engine.py:387-390`

### The openai-agents engine cannot suspend or resume [BAD] (severity: medium)
`resume` raises unconditionally. Human-in-the-loop, approvals, and pauses are langgraph-only, so the "two engines" story is really "one engine for chat, one for anything that waits". Marked as M0 scope rather than overlooked, but nothing in the public API signals which engine a feature belongs to.
```python
raise ConfigError(f"openai-agents engine (M0) has no interrupts to resume: {spec.name!r} never suspends")
yield  # pragma: no cover  -  makes this an async generator; never reached
```
Evidence: `agentdeck/adapters/engines/openai_agents/engine.py:224-225`

### `aiosqlite` is imported directly and declared nowhere [BAD] (severity: medium)
Not stdlib, not in `pyproject.toml`, and not merely re-exported: the code calls `aiosqlite.connect` and reaches into `conn._thread`. It resolves today only because `langgraph-checkpoint-sqlite` lists it (uv.lock:912, aiosqlite 0.22.1). An upstream metadata change that drops or vendors it turns the default checkpoint backend into an `ImportError` with no signal from the lockfile.
```python
import aiosqlite
from langgraph.checkpoint.sqlite import aio as sqlite_aio
...
conn._thread.daemon = True  # noqa: SLF001  -  aiosqlite exposes no public way to set this
```
Evidence: `agentdeck/adapters/engines/langgraph/checkpointer.py:152-163`

### The compile cache pins the first loop's saver, defeating `_per_loop` [BAD] (severity: medium)
`_graph_for` memoizes the compiled graph by `spec.name` with its checkpointer baked in. `_checkpointer_for` is only consulted on the first compile, so an engine instance reused across two event loops hands the second loop a graph holding the first loop's saver. That is precisely the "bound to a different event loop" failure `_per_loop` was written to prevent, reintroduced one layer up.
```python
compiled = self._compiled.get(spec.name)
if compiled is None:
    ...
    compiled = spec.native.compile(checkpointer=self._checkpointer_for(spec))
    self._compiled[spec.name] = compiled
return compiled
```
Evidence: `agentdeck/adapters/engines/langgraph/engine.py:266-274`

### `MCPLifecycle` is process-global class state with a cross-loop lock [BAD] (severity: medium)
Every field is a mutable class attribute, and `_ensure_lock` caches one `asyncio.Lock` on the class forever. `startup()` under one `asyncio.run` and `shutdown()` under another contend a lock bound to a dead loop. Same bug class the checkpointer spends thirty lines defending against, in the one adapter that has no such defense. `reset()` is labelled "tests only", which is the admission that nothing else can clear it.
```python
_lock: asyncio.Lock | None = None

@classmethod
def _ensure_lock(cls) -> asyncio.Lock:
    if cls._lock is None:
        cls._lock = asyncio.Lock()
    return cls._lock
```
Evidence: `agentdeck/adapters/tools/mcp/lifecycle.py:62-68`

### Recovery replays `call_tool`, giving tools at-least-once semantics [BAD] (severity: medium)
`_with_recovery` re-initializes and re-issues the same call when the connection looks gone. A failure on the response leg cannot be distinguished from one on the request leg, so a non-idempotent tool (charge, send, delete) can run twice. The module docstring sells this as tool calls that "self-heal" with no mention of duplicate execution, and there is no opt-out per server or per tool.
```python
async def call_tool(self, tool_name, arguments=None, meta=None) -> Any:
    return await self._with_recovery(
        lambda: super(MCPServerStreamableHttpResilient, self).call_tool(tool_name, arguments, meta)
    )
```
Evidence: `agentdeck/adapters/tools/mcp/transport.py:223-231`

### The retry policy hangs off a private SDK method [BAD] (severity: medium)
`_run_with_retries` is `agents.mcp.server`'s own private hook. Verified present in openai-agents 0.17.0 at `agents/mcp/server.py:739` and called from three sites, so the override is live today. If a version bump renames or inlines it, the same-session retry policy disappears with no error and no test to catch it: the `openai-agents==0.17.0` exact pin is the only thing holding it.
```python
async def _run_with_retries(self, func: Callable[[], Awaitable[_T]]) -> _T:
    ...
    if self._connection_gone(exc):
        raise
```
Evidence: `agentdeck/adapters/tools/mcp/transport.py:153-166`

### The whole MCP recovery path is untested [BAD] (severity: medium)
Zero references anywhere in `tests/` to `_with_recovery`, `_run_with_retries`, `_is_transient`, `_connection_gone`, `_is_session_lost`, `connect_max_attempts`, or `reconnect_max_attempts`. `tests/test_deck.py:78-79` monkeypatches `connect`/`cleanup` away, so the 234-line module's entire reason for existing (connect retry, session-loss detection, reconnect and replay) runs in no test. `wiring.py`'s two `resolve_agent_mcp_*` functions have zero hits too.
```python
monkeypatch.setattr(MCPServerStreamableHttpResilient, "connect", _connect)
monkeypatch.setattr(MCPServerStreamableHttpResilient, "cleanup", _cleanup)
```
Evidence: `tests/test_deck.py:78-79`

### No stdio MCP transport, and the error misleads [BAD] (severity: medium)
`_build_server` accepts http and streamable-http only. A standard `.mcp.json` stdio entry (`{"command": "npx", "args": [...]}`) has no `type`, so it defaults to `"http"` and then fails on the missing `url`: the operator is told the URL is absent rather than that stdio is unsupported. The docstring points at `.mcp.json`, the format where stdio is the common case.
```python
transport = (spec.get("type") or "http").lower()
if transport not in {"http", "streamable-http", "streamable_http"}:
    raise ConfigError(f"MCP server '{name}': unsupported transport '{transport}'")
url = spec.get("url")
if not isinstance(url, str):
    raise ConfigError(f"MCP server '{name}': missing `url` for http transport")
```
Evidence: `agentdeck/adapters/tools/mcp/lifecycle.py:36-41`

### Control and lease ports are never closed [BAD] (severity: medium)
`Deck.aclose()` drains sinks, closes sessions, and duck-types the store's `close`/`aclose`, but never touches the control or lease port. Both sqlite ports hold an open `sqlite3` connection plus `-wal`/`-shm` handles, and both define a `close()` that nothing in `agentdeck/` or `tests/` calls. Sequential decks in one process leak two file handles each.
```python
def close(self) -> None:   # control/sqlite/port.py:146  -  zero callers
def close(self) -> None:   # leases/sqlite/port.py:127  -  zero callers
```
Evidence: `agentdeck/deck.py:755-763`

### Three shutdown seams for one layer [BAD] (severity: low)
`SqliteEventStore.close()` is sync, `PostgresEventStore.aclose()`/`RedisEventStore.aclose()` are async, `MemoryEventStore` has neither, and `EventStorePort` declares none. The composition root pays for it with a `hasattr` probe and two `ty: ignore`s. Declaring one optional `aclose` on the port would delete this branch.
```python
if hasattr(store, "aclose"):
    await store.aclose()  # ty: ignore[call-non-callable]
elif hasattr(store, "close"):
    store.close()  # ty: ignore[call-non-callable]
```
Evidence: `agentdeck/deck.py:270-273`

### `list_runs` scans the whole namespace in all three stores [BAD] (severity: medium)
Every backend fetches every run's last lifecycle event, folds status in Python, then slices. `limit=10` on a namespace with a million runs is a million rows in postgres/sqlite and a million `GET`s in redis. Consistent across implementations, which is good parity and bad scaling: the status filter cannot be pushed down because `core.status` is deliberately the single place a status is derived.
```python
filtered = [summary for summary in summaries if status is None or summary.status is status]
return filtered if limit is None else filtered[:limit]
```
Evidence: `agentdeck/adapters/stores/redis/store.py:362-363`

### The Redis client is built with no timeouts [BAD] (severity: medium)
`Redis.from_url(url, decode_responses=True)` and nothing else: no `socket_timeout`, no `socket_connect_timeout`, no `health_check_interval`, no pool bound. SQLite pins a 5s `busy_timeout` and Postgres a 5s `lock_timeout`, both with comments about surfacing a wedged holder instead of hanging. The Redis store is the one backend where an unresponsive server blocks a request indefinitely.
```python
self._client: Redis = Redis.from_url(url, decode_responses=True)
```
Evidence: `agentdeck/adapters/stores/redis/store.py:98`

### The Redis store cannot run on Redis Cluster [BAD] (severity: low)
`_queue_writes` touches six differently-prefixed keys inside one `MULTI`, and `_stamp` watches a seventh. Without hash tags these land in different slots, so `EXEC` fails with `CROSSSLOT` on any clustered deployment. Nothing in the docstring or the settings docs says single-instance only, and this is the backend an operator reaches for precisely when scaling out.
```python
pipe.rpush(self._log_key(namespace, log_key), data)
pipe.rpush(self._run_key(namespace, log_key, event.run_id), data)
pipe.zadd(self._seq_key(namespace, log_key, event.run_id), {str(event.seq): event.seq})
```
Evidence: `agentdeck/adapters/stores/redis/store.py:142-148`
Ref: https://redis.io/docs/latest/develop/interact/transactions/

### `opentelemetry-sdk` is declared and never imported [BAD] (severity: medium)
The `observability` extra pins `opentelemetry-sdk>=1.27`, and the only trace of it in the code is a logger-name string literal. The suppression that keeps a dead Langfuse backend from spamming WARNINGs is therefore coupled to that package by exact dotted string: rename the exporter module upstream and the noise silently returns, with nothing to fail. The declaration itself is redundant, since langfuse pulls the SDK transitively.
```python
_OTLP_EXPORTER_LOGGER = "opentelemetry.exporter.otlp.proto.http.trace_exporter"
...
logging.getLogger(_OTLP_EXPORTER_LOGGER).setLevel(logging.ERROR)
```
Evidence: `agentdeck/adapters/telemetry/langfuse/client.py:36`

### `build_client` mutates process-wide state [BAD] (severity: low)
Two `os.environ.setdefault` calls and one foreign logger's level, as a side effect of constructing a client. `setdefault` means a second Deck with a different `service_name` is silently ignored, and an operator who wants exporter WARNINGs back has no setting to flip: they have to know a library raised the level. Both are documented in the docstring, neither is reversible.
```python
os.environ.setdefault("OTEL_SERVICE_NAME", settings.service_name)
os.environ.setdefault(_EXPORT_TIMEOUT_ENV, _EXPORT_TIMEOUT_SECONDS)
logging.getLogger(_OTLP_EXPORTER_LOGGER).setLevel(logging.ERROR)
```
Evidence: `agentdeck/adapters/telemetry/langfuse/client.py:65-68`

### The postgres checkpointer enters a context it never exits [BAD] (severity: medium)
`from_conn_string` is an async context manager owning the connection; the code calls `__aenter__` manually and nothing ever calls `__aexit__`. One connection per `(backend, url, loop)` is held to process exit, and `_per_loop`'s own docstring says a process running loops in a row accumulates one per loop. Known and marked (`ponytail:` at checkpointer.py:64), which makes it a deferred cost rather than an oversight, but a long-lived worker still leaks postgres backends.
```python
saver: Any = _run_sync(AsyncPostgresSaver.from_conn_string(url).__aenter__())
_run_sync(saver.setup())
```
Evidence: `agentdeck/adapters/engines/langgraph/checkpointer.py:190-192`

### Both transport retry loops sleep after the final attempt [BAD] (severity: low)
`connect` logs "retrying in 4.0s", sleeps four seconds, then exits the loop and raises. Same shape in `_with_recovery`. With the defaults that is four wasted seconds on every hard connect failure at boot and a log line promising a retry that never happens. The `for`/`else` idiom or a bounds check on `attempt` removes both.
```python
backoff = self.connect_backoff_base_seconds * (2 ** (attempt - 1))
logger.warning("MCP connect to %s failed (attempt %d/%d)  -  retrying in %.1fs", ...)
await asyncio.sleep(backoff)
if last_exc is None:
    raise RuntimeError(f"MCP connect to {self.name} made no attempt")
raise last_exc
```
Evidence: `agentdeck/adapters/tools/mcp/transport.py:95-106`

### A failed MCP connect is dropped without cleanup [BAD] (severity: low)
`startup` pops the server out of `_servers` on failure, and `shutdown` iterates `_connected` only, so a `connect()` that raised partway through its `AsyncExitStack` has nothing left holding a reference that could unwind it. Bounded by the number of configured servers, so this is untidy rather than dangerous, but the streams stay open until GC.
```python
cls._failed[name] = exc
# Soft-fail: unreachable for the rest of the process (no boot-time reconnect).
cls._servers.pop(name, None)
continue
```
Evidence: `agentdeck/adapters/tools/mcp/lifecycle.py:125-129`

### Dead public surface in the MCP adapter [BAD] (severity: low)
`MCPLifecycle.configured_names`, `failed_names`, and `failure_reason` have zero callers in `agentdeck/` or `tests/`. `resolve_agent_mcp_servers` is exported from the package `__init__` and called by nobody either: `authoring.compile` uses `resolve_agent_mcp_status` directly. Four public entry points maintained for no consumer.
```python
@classmethod
def failure_reason(cls, name: str) -> BaseException | None:
    return cls._failed.get(name)
```
Evidence: `agentdeck/adapters/tools/mcp/lifecycle.py:82-84`

### Internal tuning constants and log keys are package exports [BAD] (severity: low)
`MAX_OPEN_RUNS`/`MAX_OPEN_CALLS`, `DURABLE_KEY`/`REPORTER_KEY`/`STREAM_WRITE_KEY`, and `MCP_SERVER_NAMES_KEY` all sit in adapter `__init__` exports. Both sink bounds are already constructor parameters, so the constants need not leave the module at all. This is the "never leak internal plumbing (log keys) into public user APIs" line in CLAUDE.md, leaked at three adapters.
```python
from agentdeck.adapters.telemetry.langfuse.sink import MAX_OPEN_CALLS, MAX_OPEN_RUNS, LangfuseSink
```
Evidence: `agentdeck/adapters/telemetry/langfuse/__init__.py:5`

### `ExecutionStore._local` grows without bound [BAD] (severity: low)
One `SQLiteSession` per `(namespace, log_key)` for the process's life, cleared only in `aclose`. On the no-Redis path a long-running server accumulates one in-memory SQLite database per conversation it has ever served. Marked (`ponytail:` at sessions.py:107) as acceptable because the local path is meant for tests, but `AGENTDECK_SESSION` unset is also the out-of-the-box default.
```python
self._local: dict[str, SQLiteSession] = {}
...
return self._local.setdefault(key, SQLiteSession(key))
```
Evidence: `agentdeck/adapters/engines/openai_agents/sessions.py:98-104`

### Checkpoint backends and event backends disagree [BAD] (severity: low)
Event stores: memory, sqlite, redis, postgres. Checkpoint backends: memory, sqlite, postgres. An operator who chose Redis for the event log must stand up a second database, Postgres, purely for durable workflow state. Defensible (LangGraph ships no Redis saver in the declared deps) but the asymmetry is nowhere stated at the settings level.
```python
raise ValueError(
    f"unknown checkpoint backend {backend!r}; expected sqlite, postgres, or memory "
)
```
Evidence: `agentdeck/adapters/engines/langgraph/checkpointer.py:128-131`

## Bottom line

The store adapters are the strongest code in the repo: correct transaction posture on all three real backends, escaped keys, secret-aware errors, and full port parity with a contract suite behind it. The two failures that matter are structural rather than local, and both bite the same user: the deployment that outgrows one process gets a distributed event log with no distributed cancel or lease, and the langgraph engine that runs its workflows reports zero cost for all of them. Fix the session-key escaping first (a one-line fix and a tenant-isolation bug), then decide whether the MCP adapter's untested recovery path is a feature or a liability.
