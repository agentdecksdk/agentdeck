# AgentDeck SDK: Architecture Audit

15,032 LOC across five declared rings (core, runtime, adapters, authoring, surfaces) plus eight top-level modules, with 11 import-linter contracts that all pass. The inner rings are genuinely clean: `core/` is stdlib-plus-pydantic and the ports are small, well-specified contracts, which is rarer than it sounds. The failures are all at the outer edge: a second execution path that bypasses the Runtime entirely, a port with no production consumers next to the process-global singleton that actually does the work, and a contract set built from deny-lists that leaves four of six runtime modules and the composition root unconstrained.

## Findings

### core is genuinely pure, and machine-enforced [GOOD] (severity: high)
Every module under `agentdeck/core/` imports stdlib, pydantic, and other `core` modules. Nothing else. Verified by grep over all imports and by `lint-imports`, which reports 11 kept, 0 broken.

```ini
[importlinter:contract:core-is-engine-free]
name = core imports no engine, surface or outer ring
type = forbidden
source_modules =
    agentdeck.core
forbidden_modules =
    agents
    langgraph
    fastapi
```
Evidence: `.importlinter:22`

### EnginePort states its whole contract in two methods [GOOD] (severity: high)
Two abstract methods and a docstring that pins every way a run may end, including what happens to payloads yielded after a terminal one. An adapter author has no room to guess.

```python
    A run ends in exactly one of three ways: a terminal payload (``run.completed`` /
    ``run.failed`` / ``run.cancelled``), a suspending one (``run.interrupted`` / ``run.paused``)
    whose terminal event comes after resume, or a raised exception. Stopping after anything else
    is a contract violation the Runtime records as ``run.failed``. A terminal payload ends the
    run there and then  -  anything yielded after one is discarded rather than logged.
```
Evidence: `agentdeck/core/ports/engine.py:24`

### Ports are minimal, and no adapter imports another [GOOD] (severity: medium)
Six ports, 543 LOC total, 2 to 4 methods each except the store. A grep for `from agentdeck.adapters` inside `agentdeck/adapters/` returns only intra-package imports: 12 adapter directories, zero sibling edges.

```python
class ControlPort(ABC):
    @abstractmethod
    async def signal(self, id: str, sig: Signal, reason: str | None = None) -> None:
    @abstractmethod
    async def poll(self, id: str) -> ControlSignal | None:
    @abstractmethod
    async def consume(self, id: str, expected: Signal) -> bool:
```
Evidence: `agentdeck/core/ports/control.py:23`

### Optional extras are actually optional [GOOD] (severity: high)
`import agentdeck` loads none of fastapi, redis, psycopg, langfuse, or aiosqlite. Each optional package is either function-local at its import site or eager inside a submodule reached only by a lazy import in `composition.py`.

```python
    if scheme in ("redis", "rediss"):
        try:
            from agentdeck.adapters.stores.redis import RedisEventStore
        except ImportError as exc:
            raise ImportError(
                'the redis event store needs the redis client  -  install the "redis" extra: '
                f'pip install "agentdeck-sdk[redis]"  -  see {_STORE_DOCS}'
            ) from exc
        return RedisEventStore(events.url)
```
Evidence: `agentdeck/composition.py:238`

### Every contract carries its own justification [GOOD] (severity: medium)
All 11 contracts are prefixed with why they exist, which issue introduced them, and what was deliberately left out. The exclusions are the valuable part: a reviewer can tell an oversight from a decision.

```ini
# ``agentdeck.authoring`` is deliberately absent from this contract's
# source_modules  -  it compiles ``Workflow``/``Agent`` declarations for either engine, so it
# needs both `agents` and `langgraph` directly (amended #164: authoring/ imports core/ plus
# the engine SDKs it compiles to, not core/ alone).
```
Evidence: `.importlinter:110`

### The public top-level surface is 13 names and no plumbing [GOOD] (severity: medium)
No store, resolver, runtime, context or log key reaches `agentdeck/__init__.py`. `Deck`'s docstring states the omissions as policy rather than leaving them accidental.

```python
    There is no ``deck.observers`` property, for the reason there is no ``runtime`` or ``store``:
    nothing needs one, and a property is additive later while removing one is not.
```
Evidence: `agentdeck/deck.py:373`

### InvocableSpec is a real engine-neutral seam [GOOD] (severity: medium)
One frozen model with an opaque `native` payload. Three engine adapters read it, each type-checking its own shape and raising a `ConfigError` naming what it got. That is the whole substitution boundary, in 39 lines.

```python
class InvocableSpec(CoreModel):
    """Engine-neutral description: the authoring layer compiles to this, engines read it.

    ``engine`` selects the adapter; ``native`` is that adapter's own payload and nothing
    outside it may look inside.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)
    engine: str
    native: Any = None
```
Evidence: `agentdeck/core/invocable.py:23`

### Agent.run() and Workflow.run() are a second execution path [BAD] (severity: high)
Both headline exported classes carry a public `run()` that bypasses the Runtime: no event log, no store, no control port, no lease, no observers, no session claim, and a different return type from `Deck.run()`. The cost is admitted in shipped code, where the fake-model harness has to patch two provider sites because either one can reach a real endpoint.

```python
# Two places still resolve a run config: the Runtime plays a turn through the openai-agents
# adapter, while a workflow node driving an agent of its own still goes through the
# direct-call runner. Patching only one would pass a test while the other reached for a
# real endpoint.
_PROVIDER_TARGETS = (
    "agentdeck.authoring.runners.agent.OpenAIProvider",
    "agentdeck.adapters.engines.openai_agents.runconfig.OpenAIProvider",
)
```
Evidence: `agentdeck/testing.py:44`, `agentdeck/authoring/agent.py:156`, `agentdeck/authoring/workflow.py:112`

### ToolSourcePort has zero production consumers [BAD] (severity: high)
`ToolSourcePort`, `MCPToolSource` and `ToolSet` are referenced nowhere in `agentdeck/` outside the port definition, the adapter that implements it, and its own test file. The Runtime never resolves tools through the port. This is precisely what the project's own architecture standard warns against.

```python
class MCPToolSource(ToolSourcePort):
    def resolve(self, spec: InvocableSpec) -> ToolSet:
```
Evidence: `agentdeck/adapters/tools/mcp/source.py:23`, `docs/engineering/architecture.md:86`

### The real MCP path is a process-global class singleton [BAD] (severity: medium)
What production actually uses instead of the port: five mutable class attributes, all-classmethod access, and a `reset()` for tests. Both the composition root and the authoring ring read and mutate it, so MCP state is shared by every Deck, agent and compile pass in the process.

```python
class MCPLifecycle:
    """Process-wide registry of MCP servers, keyed by config name."""

    _servers: dict[str, MCPServer] = {}
    _failed: dict[str, BaseException] = {}
    _connected: set[str] = set()
    _config: dict[str, dict[str, Any]] = {}
    _lock: asyncio.Lock | None = None
```
Evidence: `agentdeck/adapters/tools/mcp/lifecycle.py:50`

### import agentdeck eagerly loads both engine SDKs [BAD] (severity: medium)
`deck.py` imports both engine adapters at module scope, so a plain `import agentdeck` costs 0.96s and 2136 modules and pulls `agents`, `openai`, `langgraph` and `langchain_core` whether or not the caller uses either engine. The architecture doc's own test ("removing one integration should not damage unrelated functionality") fails for exactly these two.

```python
from agentdeck.adapters.engines.langgraph import LangGraphEngine
from agentdeck.adapters.engines.openai_agents import ExecutionStore, OpenAIAgentsEngine, SessionFactory
```
Evidence: `agentdeck/deck.py:48`, `docs/engineering/architecture.md:76`

### Every contract is a deny-list, so new modules default to unconstrained [BAD] (severity: medium)
All 11 contracts are `type = forbidden`. There is no `layers` contract, so the ring order in `architecture.md` is enforced only where someone remembered to enumerate it. `agentdeck.composition`, `agentdeck.observers`, `agentdeck.cli` never appear as a source module, and the runtime contract names two of six runtime modules.

```ini
source_modules =
    agentdeck.runtime.service
    agentdeck.runtime.dispatch
forbidden_modules =
    agentdeck.adapters
    agentdeck.authoring
```
Evidence: `.importlinter:48`
Ref: https://import-linter.readthedocs.io/en/stable/contract_types.html

### runtime/discovery.py imports the authoring ring [BAD] (severity: medium)
The documented arrow is Authoring to Runtime to Core. `runtime/discovery.py` inverts it at module scope, and no contract catches it: the runtime deny-list covers only `service` and `dispatch`, and the two contracts that name `agentdeck.runtime` whole forbid `mcp` and `langfuse` only.

```python
from agentdeck.authoring.agent import Agent
from agentdeck.authoring.compile import compile_agent, link_handoffs
from agentdeck.authoring.graphs import bridge_context_nodes
from agentdeck.authoring.workflow import Workflow
```
Evidence: `agentdeck/runtime/discovery.py:14`, `docs/engineering/architecture.md:11`

### The composition root reads spec.native, against that field's stated invariant [BAD] (severity: medium)
`InvocableSpec` says nothing outside the owning adapter may look inside `native`. `Deck.__aenter__` unpacks it for every agent and hands the raw `agents.Agent` objects to the authoring layer to mutate in place. The path is silently openai-agents-only: an agent compiled for any other engine would hand `refresh_mcp_status` the wrong type.

```python
            invocables = self._invocables
            assert invocables is not None  # build() just above guarantees this
            agents = list(self._agents.values())
            refresh_mcp_status({name: invocables[name].native for name in self._agents}, agents)
```
Evidence: `agentdeck/deck.py:675`, `agentdeck/core/invocable.py:26`

### Authoring reaches into adapter implementation modules, unregistered [BAD] (severity: medium)
Five edges from `authoring/` into adapter internals, three of them to modules the adapter package does not export. The registry that `architecture.md` calls binding permits authoring only "provider SDK types required for authoring/compilation", and `STREAM_CONFIGURABLE_KEY` and `resolve_checkpointer` are neither.

```python
from agentdeck.adapters.engines.langgraph.checkpointer import resolve_checkpointer
from agentdeck.adapters.tools.mcp.wiring import mcp_status_banner, resolve_agent_mcp_status
```
Evidence: `agentdeck/authoring/compile.py:47`, `agentdeck/authoring/nodes.py:19`, `agentdeck/authoring/runners/agent.py:25`, `docs/engineering/import-boundaries.md:27`

### Checkpoint resolution is duplicated verbatim in a second ring [BAD] (severity: medium)
`composition.resolve_checkpoint` exists for this and is pinned by two tests. `authoring/compile.py` re-derives the same three lines from the process-global settings cache instead of calling it, so the direct-call `Workflow.run()` path and the Runtime path resolve durability independently.

```python
    checkpoint = get_settings().checkpoint
    scheme, rest = parse_backend_url(checkpoint.url)
    backend = "postgres" if scheme == "postgresql" else scheme
    path_or_dsn = rest if backend == "sqlite" else checkpoint.url
    return graph.compile(checkpointer=resolve_checkpointer(backend, path_or_dsn))
```
Evidence: `agentdeck/authoring/compile.py:84`, `agentdeck/composition.py:152`

### openai-agents plumbing sits on the generic Deck's public API [BAD] (severity: medium)
`session_factory=` is a documented constructor keyword typed as an openai-agents adapter class, and `session_for()` returns the raw SDK `agents.memory.session.Session`. Both are engine-specific concepts on the class whose stated job is hiding infrastructure, and neither has a langgraph counterpart.

```python
    def session_for(self, session_id: str) -> Session:
        """Conversation memory for ``session_id``  -  the engine's own store, so a turn started
        here and one started over HTTP land in the same conversation."""
        return self._ensure_sessions().session_for(_new_context(session_id))
```
Evidence: `agentdeck/deck.py:1095`, `agentdeck/deck.py:399`

### The import-boundary registry architecture.md calls binding is a stub [BAD] (severity: medium)
`architecture.md` requires every external-dependency exception to be "represented in import-boundaries.md". That file carries three vague rows, an admonition to populate it from the repository, and no enforcement column that names an actual check. The five authoring-to-adapter edges above are the gap this would have caught.

```markdown
## Current exceptions

> Populate this table from the current repository before making this registry binding in CI.

| Path | Allowed external imports | Reason | Enforcement |
```
Evidence: `docs/engineering/import-boundaries.md:20`

### deck.py is a 1413-LOC god module [BAD] (severity: medium)
`Deck` alone is 765 lines and roughly 40 methods spanning catalog validation, context-type checking, workflow-as-tool compilation, lifecycle, a timer sweeper, session store construction, and ASGI app construction. Ten of those methods are thin Runtime delegations that exist only because `Run` and `Runs` need a friend class Python does not have.

```python
    async def _pause(self, run_id: str, reason: str | None = None, namespace: str | None = None) -> bool:
        """Implementation behind :meth:`Run.pause`, and the v1 wire's blind signal (naming no
        namespace is what an unnamespaced HTTP caller means, not an omission here)."""
        return await self._require_open().signal(run_id, Signal.PAUSE, reason, namespace=namespace)

    async def _cancel(self, run_id: str, reason: str | None = None, namespace: str | None = None) -> bool:
        """Implementation behind :meth:`Run.cancel`, and the v1 wire's blind signal."""
        return await self._require_open().signal(run_id, Signal.CANCEL, reason, namespace=namespace)
```
Evidence: `agentdeck/deck.py:944`

### DURABLE_KEY is duplicated with a comment claiming a test that does not exist [BAD] (severity: medium)
The runtime writes `metadata["durable"]` from its own constant and the langgraph adapter reads it from a second literal. The comment asserts a test pins the two together. `test_kind_to_engine_names_match_the_adapters` asserts the two engine names and nothing else, and no test anywhere compares the two `DURABLE_KEY` values. Drift is caught only incidentally, by `tests/test_langgraph_checkpointer.py` building specs from the runtime's constant and feeding them to the adapter, never asserted directly. Contrast `STREAM_WRITE_KEY`, the same cross-ring duplication, which does have its equality assertion at `tests/test_serve_compat.py:111`.

```python
# Where a workflow's opt-in durability travels to the engine that acts on it: the langgraph
# adapter reads ``spec.metadata[DURABLE_KEY]`` to decide whether to resolve the configured
# checkpointer at all. Spelled out rather than imported, for the reason above; the same test
# that pins the engine names pins this one to the adapter's own constant.
DURABLE_KEY: Final[str] = "durable"
```
Evidence: `agentdeck/runtime/discovery.py:41`, `agentdeck/adapters/engines/langgraph/engine.py:75`, `tests/test_invocable_registry.py:177`

### EventStorePort fuses three concerns into eight abstract methods [BAD] (severity: medium)
Append and read (event log), `claim_start` and `claim_resume` (session concurrency control), `list_runs` and `find_by_key` (run index). Four store adapters and every test double must implement all eight to get any one of them, which is why a signature change here breaks unrelated test subclasses in subprocesses.

```python
class EventStorePort(ABC):
    # abridged: signatures only, at 51 / 69 / 84 / 96 / 105 / 162 / 186 / 202
    async def append(self, log_key: str, payloads: Sequence[KnownPayload], ctx: RunContext, origin: str)
    async def read(self, log_key: str, ctx: RunContext, offset: int = 0, limit: int | None = None)
    async def read_run(self, log_key: str, run_id: str, ctx: RunContext, from_seq: int = 0)
    async def claim_start(
    async def claim_resume(
    async def list_runs(
    async def find_by_key(self, ctx: RunContext, key: str) -> str | None:
```
Evidence: `agentdeck/core/ports/store.py:51`

### The surfaces ring ships two test-only HTTP apps and not the real one [BAD] (severity: low)
`surfaces/serve/app.py` and `surfaces/serve/workflows.py` build FastAPI apps that no production entry point imports; only tests call `build_app`. The surface `agentdeck serve` actually runs is `agentdeck/serve.py:101`, which sits outside `agentdeck.surfaces` and therefore outside the `surfaces-are-adapter-free` contract.

```python
"""One crude SSE route for v2 runs  -  separate from v1's ``serve.py``, which stays
untouched. No auth, no discovery: the composition root hands in an already-wired
``Runtime``. Skeleton component: hardened or discarded at the M0 review, not polished now.
"""
```
Evidence: `agentdeck/surfaces/serve/app.py:1`, `agentdeck/serve.py:101`, `.importlinter:95`

### Adapter package __init__ files export internal constants with no consumers [BAD] (severity: low)
`MAX_OPEN_CALLS` and `MAX_OPEN_RUNS` are tuning defaults read only inside `sink.py`, promoted to the telemetry package's public surface. Same pattern for `MCP_SERVER_NAMES_KEY`, `DURABLE_KEY` and `REPORTER_KEY`: internal side-channel keys re-exported at the package boundary.

```python
from agentdeck.adapters.telemetry.langfuse.sink import MAX_OPEN_CALLS, MAX_OPEN_RUNS, LangfuseSink

__all__ = [
    "MAX_OPEN_CALLS",
    "MAX_OPEN_RUNS",
```
Evidence: `agentdeck/adapters/telemetry/langfuse/__init__.py:4`

### One Deck per process, enforced by a module global [BAD] (severity: low)
A module-level `_live_deck` refuses a second Deck. The limit is real and follows from the process-global MCP registry, the `sys.modules` bundle aliasing in `mount_project_dir`, and the `lru_cache`d settings that authoring reads at compile time. Low because the error message is exemplary: it names both projects, the cause, the fix, and the tracking issue.

```python
    raise ConfigError(
        f"a Deck is already live in this process ({_origin(_live_deck._project_path)}); "
        f"agentdeck v3 supports one Deck per process, so this one ({incoming}) would read the "
        "first one's bundles and share its MCP servers. Close the first with "
        "`await deck.aclose()` before constructing another. Two decks side by side is "
        "deferred  -  agentdeck issue #213."
    )
```
Evidence: `agentdeck/deck.py:324`

### Nineteen percent of the SDK lives outside the five declared rings [BAD] (severity: low)
Eight top-level modules hold 2839 of 15,032 LOC: `deck.py`, `serve.py`, `testing.py`, `composition.py`, `observers.py`, `mcp.py`, `errors.py`, `cli.py`. Some placements are defensible (`errors.py` as the one cross-cutting module, protected by its own contract). Others are not: `serve.py` is a surface, `cli.py` is a surface, `observers.py` is telemetry wiring, and `surfaces/` holds thinner versions of the first two.

```ini
[importlinter]
root_package = agentdeck
```
Evidence: `.importlinter:1`, `agentdeck/serve.py:101`, `agentdeck/observers.py:87`

## Bottom line

The inside of this system is better than most: a pure core, ports whose docstrings actually specify their contracts, one real engine-neutral seam, and optional dependencies that are genuinely optional and measurably so. The outside is where it frays, and the theme is consistent: the layering is defended by hand-enumerated deny-lists and copied constants rather than by structure, so the arrows that nobody enumerated point the wrong way and the abstraction that was built (`ToolSourcePort`) sits unused beside the global singleton that does the work. Three fixes would move this from good to genuinely tight: retire the direct-call `Agent.run()`/`Workflow.run()` path, replace the 11 deny-lists with one `layers` contract plus narrow documented exceptions, and either wire `ToolSourcePort` in or delete it along with `MCPLifecycle`'s class-level state.
