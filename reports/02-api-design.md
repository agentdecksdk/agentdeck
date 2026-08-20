# AgentDeck SDK: Public API Design and Ergonomics

AgentDeck's user-facing surface is small, deliberate, and in places genuinely better than the two SDKs it wraps: the `Context[T]` injection story and the `Run`/`Runs` handle split are the work of someone who has felt the pain of `RunContextWrapper` and `config={"configurable": {"thread_id": ...}}`. The failures are not sprawl but leakage: declared pydantic models come back as plain dicts, the most-used await path raises outside the advertised exception hierarchy, and the first objects a user touches carry a second `run()` that silently skips the entire runtime. Judged against the OpenAI Agents SDK and LangGraph, the composition story is ahead and the typing story is behind.

## Findings

### Context[T] is the right abstraction, and it is invisible to the model [GOOD] (severity: high)
One portable context type above two engines, injected into plain functions that were never decorated, and absent from the schema the model sees. This is the single best idea in the API.

```python
    if analysis.context_parameter is None:
        return function_tool(target, failure_error_function=_tool_failure)
    return function_tool(_bridge(analysis), failure_error_function=_tool_failure)
```
Evidence: `agentdeck/authoring/tools.py:67`, `agentdeck/core/context.py:120`
Ref: https://openai.github.io/openai-agents-python/

### Tools are plain functions, with the SDK object still accepted [GOOD] (severity: medium)
`tools=[find_slots]` needs no decorator, no wrapper type, and no import from `agents`. A pre-built SDK tool passes straight through untouched, so interop is preserved rather than traded away.

```python
def compile_tool(target: Callable[..., Any], *, context_type: object | None = None) -> FunctionTool:
```
Evidence: `agentdeck/authoring/tools.py:40`, `agentdeck/authoring/agent.py:66`

### Run and Runs split the collection from the handle, with no duplicated verbs [GOOD] (severity: high)
`deck.runs` starts/finds; every operation on a run lives on the handle it hands back. No `deck.pause(run_id)` shadowing `run.pause()`, and no thread-id bookkeeping pushed onto the caller the way LangGraph's config dict does.

```python
    async def pause(self, reason: str | None = None) -> bool:
    async def resume(self) -> None:
    async def cancel(self, reason: str | None = None) -> bool:
    async def answer(self, value: Any) -> None:
```
Evidence: `agentdeck/deck.py:1184`, `agentdeck/deck.py:1306`
Ref: https://langchain-ai.github.io/langgraph/

### Starting a run and watching one are separate acts [GOOD] (severity: high)
Closing the `stream()` generator stops watching, never the run, and `Run.events(follow=True)` can tail a run this process did not start. Most agent SDKs conflate the two and lose the run when the consumer walks away.

```python
    def events(self, *, from_seq: int = 0, follow: bool = False) -> AsyncIterator[Event]:
```
Evidence: `agentdeck/deck.py:1222`, `agentdeck/deck.py:923`

### Errors state what happened, why, and the exact call that fixes it [GOOD] (severity: high)
Not one or two showpieces: the pattern holds across the refusals a user actually hits. The messages name the offending object, the reason, the fix, and where relevant the tracking issue.

```python
        f"a Deck is already live in this process ({_origin(_live_deck._project_path)}); "
        f"agentdeck v3 supports one Deck per process, so this one ({incoming}) would read the "
        "first one's bundles and share its MCP servers. Close the first with "
        "`await deck.aclose()` before constructing another."
```
Evidence: `agentdeck/deck.py:324`, `agentdeck/errors.py:103`, `agentdeck/authoring/workflow.py:144`, `agentdeck/deck.py:120`

### RunSuspendedError carries the payload the caller needs next [GOOD] (severity: medium)
Awaiting a suspended run raises rather than hanging forever, names the verb that would work, and attaches the interrupt payload so the caller does not have to go re-read it.

```python
        verb = "run.answer(...)" if status is RunStatus.WAITING_ANSWER else "run.resume()"
        super().__init__(f"run {run_id!r} is {status.value}, not done: call {verb} instead of awaiting it.")
        self.status = status
        self.pending = pending
```
Evidence: `agentdeck/errors.py:102`

### Declarations are immutable, and omission rather than falsiness defers to the base [GOOD] (severity: medium)
`Agent`/`Workflow` refuse mutation with a message naming the fix, and an explicitly passed empty value beats the base's non-empty one. That second detail is the kind of thing most config APIs get wrong.

```python
    def __setattr__(self, key: str, value: Any) -> None:
        raise AttributeError(f"Agent is immutable; build a new one instead of setting {key!r}.")
```
Evidence: `agentdeck/authoring/agent.py:162`, `agentdeck/authoring/agent.py:120`

### Ambiguous lookups are refused, not guessed [GOOD] (severity: medium)
`runs.get()` takes exactly one of an id or a key. Duplicate names in `agents=` are refused instead of collapsing to whichever came last.

```python
        if (id is None) == (key is None):
            raise ValueError("deck.runs.get(...) takes exactly one of a positional id, or key=.")
```
Evidence: `agentdeck/deck.py:1371`, `agentdeck/deck.py:283`

### The hooks bridge tracks the SDK instead of hardcoding its method list [GOOD] (severity: low)
Derived from the installed `AgentHooks` class, so a hook the upstream SDK adds is bridged without an edit here. Small, and exactly the maintenance instinct a wrapper library needs.

```python
HOOK_METHODS: frozenset[str] = frozenset(
    name for name, _ in inspect.getmembers(AgentHooks, inspect.isfunction) if not name.startswith("_")
)
```
Evidence: `agentdeck/authoring/hooks.py:33`

### The shipped test harness covers both in-process and out-of-process [GOOD] (severity: medium)
`patch_model` for a test that builds a Deck directly, `scripted_model_server` for one that must cross a real HTTP client or subprocess. Neither wrapped SDK ships a comparable public helper, and the absence of one is a standard reason downstream agent tests end up hitting a real endpoint.

```python
@contextmanager
def patch_model(model: Model | Callable[[], Model]) -> Iterator[None]:
```
Evidence: `agentdeck/testing.py:224`, `agentdeck/testing.py:332`

### Capability arguments coerce, and validate where it matters [GOOD] (severity: low)
`skills=` takes a string, a path, a sequence, or the object. Validation is deferred to `build()` and then refuses a `SKILL.md` whose frontmatter name does not match its directory, with a docs link.

```python
def _coerce_skills(value: str | Path | Sequence[str | Path] | Skills | None) -> Skills | None:
```
Evidence: `agentdeck/deck.py:291`, `agentdeck/skills/__init__.py:106`

### The infrastructure properties are deliberately absent [GOOD] (severity: medium)
No `deck.runtime`, no `deck.store`, no `deck.observers`, and the reasoning is recorded: a property is additive later, removing one is not. The abstraction actually deletes complexity rather than relocating it.

```python
    Public properties are :attr:`agents`, :attr:`workflows`, :attr:`skills` and :attr:`settings`
    only  -  never ``runtime`` or ``store``, the infrastructure this class exists to hide.
```
Evidence: `agentdeck/deck.py:377`

### You declare a pydantic model and get a dict back [BAD] (severity: high)
Both typed-output paths dump and never rehydrate: an `output_type=Booking` agent's result and a workflow's mandatory `state=` model both arrive as `JsonData`, with no `final_output_as` or equivalent anywhere in the package. The OpenAI Agents SDK hands back the instance; this hands back its JSON and leaves every caller to re-validate by hand.

```python
    data = next((block.data for block in payload.output if isinstance(block, DataBlock)), None)
    if isinstance(deck._root(event.origin), Agent):
        output = (data if data is not None else "".join(...))
        return TurnResult(output=output, usage=payload.usage, ...)
    return data
```
Evidence: `agentdeck/deck.py:1121`, `agentdeck/adapters/engines/openai_agents/engine.py:313`, `agentdeck/core/content.py:97`
Ref: https://openai.github.io/openai-agents-python/

### The most-used await path raises outside the advertised hierarchy [BAD] (severity: high)
`errors.py` opens by promising a single `except AgentdeckError` and lists the deliberate exceptions (pydantic validator bodies, missing-path faults); a failed or cancelled run is neither, yet `await run` raises bare `RuntimeError` for both. The `__await__` docstring documents this behavior, which makes it a contradiction rather than an oversight.

```python
        if isinstance(payload, RunFailed):
            raise RuntimeError(f"run {self.id!r} failed: {payload.message}")
        if isinstance(payload, RunCancelled):
            raise RuntimeError(f"run {self.id!r} was cancelled: {payload.reason}")
```
Evidence: `agentdeck/deck.py:1294`, `agentdeck/errors.py:1`, `agentdeck/deck.py:1252`

### Agent.run() and Workflow.run() are traps wearing the name of the main path [BAD] (severity: high)
`principles.md` permits escape hatches, but this one shares the verb `run` on the first two objects a user constructs, silently skips the event log, observers, cancellation and persistence, and speaks a different vocabulary besides: `thread_id` and `resume(thread_id, value)` against the runtime's `session_id` and `run.answer(value)`. A user who writes `await agent.run("hi")` gets a plausible answer and none of the product.

```python
    async def run(self, message: Any = None, **runner_options: Any) -> Any:
        """One-shot headless run (no event log); returns the SDK ``RunResult``."""
```
Evidence: `agentdeck/authoring/agent.py:156`, `agentdeck/authoring/workflow.py:112`, `agentdeck/authoring/workflow.py:162`, `docs/engineering/principles.md:64`

### deck.run() is annotated `TurnResult | Any`, which is Any [BAD] (severity: high)
The union collapses under any type checker, so the primary entry point of the SDK returns an unchecked value. The docstring knows the real contract (a `TurnResult` for an agent, state or an `InterruptResult` for a workflow) and the annotation expresses none of it.

```python
    ) -> TurnResult | Any:
```
Evidence: `agentdeck/deck.py:878`
Ref: https://docs.python.org/3/library/typing.html

### Errors the documented paths raise are not exported from the package root [BAD] (severity: medium)
`RunSuspendedError`, `RunStateError`, and `DuplicateKeyError` are all named in the docstrings of `run()`, `runs.start()`, and `Run.answer()`, and none of them are in `agentdeck.__all__`. Catching what the top-level API throws requires knowing to reach into `agentdeck.errors`.

```python
__all__ = [
    "Agent", "AgentdeckError", "ConfigError", "Context", "ContextTypeError",
    "Deck", "NotFoundError", "Run", "SessionBusyError", "SkillError",
    "StoreError", "TurnResult", "Workflow", "__version__",
]
```
Evidence: `agentdeck/__init__.py:37`, `agentdeck/errors.py:109`, `agentdeck/deck.py:1337`

### Known concrete return types are annotated Any [BAD] (severity: medium)
Three public methods return well-known classes and declare none of them: `agents.Agent`, a compiled LangGraph graph, and an ASGI app. All three files already use `TYPE_CHECKING` imports for exactly this purpose, so the cost of fixing it is one import block.

```python
    def build(self) -> Any:        # agent.py:145,  really agents.Agent
    def build(self) -> Any:        # workflow.py:99, really a compiled StateGraph
    def asgi(self) -> Any:         # deck.py:1100,   really a FastAPI/ASGI app
```
Evidence: `agentdeck/authoring/agent.py:145`, `agentdeck/authoring/workflow.py:99`, `agentdeck/deck.py:1100`

### Two front doors take untyped `**kwargs` [BAD] (severity: medium)
`Deck.from_project()` is half the advertised construction story and forwards everything but `path` through `**kwargs: Any`, so `context=`, `observers=`, and `session_factory=` are invisible to a checker and to autocomplete. `Workflow.run`/`run_stream`/`resume` do the same with `**runner_options`.

```python
    def from_project(cls, path: str | Path = PROJECT_DIR, **kwargs: Any) -> Deck:
```
Evidence: `agentdeck/deck.py:449`, `agentdeck/authoring/workflow.py:112`

### The `_UNSET` sentinel makes required parameters look optional [BAD] (severity: medium)
`name: str = _UNSET` where `_UNSET: Any = object()` tells a checker that `Agent()` is valid; it raises at runtime instead. The same pattern hides `Workflow`'s required `name` and `state`, so three required arguments across the two authoring entry points are statically invisible.

```python
_UNSET: Any = object()
...
        name: str = _UNSET,
...
        if not resolved_name:
            raise ValueError("Agent(name=...) is required (directly, or via base=).")
```
Evidence: `agentdeck/authoring/agent.py:20`, `agentdeck/authoring/agent.py:107`, `agentdeck/authoring/workflow.py:74`

### Authoring-time misuse raises stdlib exceptions, not the taxonomy [BAD] (severity: medium)
A missing `Agent(name=)`, a missing `Workflow(state=)`, a durable workflow with no `thread_id`, a bad `runs.get()` call and `LoadFileNode`'s two refusals fit neither declared exception in `errors.py`, yet each raises `ValueError`, `TypeError` or `RuntimeError`. `ConfigError` exists and is used correctly elsewhere in the same files, which makes this inconsistency rather than policy.

```python
            raise ValueError(f"Workflow(name={resolved_name!r}) needs state=... (a pydantic model).")
```
Evidence: `agentdeck/authoring/workflow.py:90`, `agentdeck/authoring/agent.py:126`, `agentdeck/authoring/workflow.py:152`, `agentdeck/deck.py:1372`, `agentdeck/authoring/nodes.py:51`

### InterruptResult is a TypedDict that leaks the engine's thread id [BAD] (severity: medium)
`Run.pending()` and `deck.run()` on a workflow both hand back this dict, whose own docstring says `thread_id` "stays internal to the engine" and then ships it as a required key of the public return type. Callers also probe `result["type"] == "interrupt"` with string keys to tell an interrupt from a final state, where a class would give them `isinstance`.

```python
class InterruptResult(TypedDict):
    type: Literal["interrupt"]
    payload: Any
    thread_id: str
    id: NotRequired[str | None]
```
Evidence: `agentdeck/authoring/interrupts.py:17`, `agentdeck/deck.py:1203`

### One Deck per process is enforced by a module global [BAD] (severity: medium)
A library that refuses a second instance is a real constraint on its hosts: a multi-tenant server, a notebook re-running a cell, or a single test that leaks an open deck and poisons every test after it. Both wrapped SDKs let you instantiate freely; the refusal here is honest and well-worded, and it is still a singleton in a library.

```python
_live_deck: Deck | None = None
```
Evidence: `agentdeck/deck.py:309`, `agentdeck/deck.py:333`

### Workflow.state is typed `type` while the code demands `type[BaseModel]` [BAD] (severity: medium)
Two runtime checks insist on a pydantic model and the annotation admits any class at all. `pydantic.BaseModel` is already imported at module scope in this file, so `type[BaseModel]` would move both failures to the type checker at zero cost.

```python
    state: type
...
            raise TypeError(f"{self.name}.state must be a Pydantic model to be exposed as a tool; got {self.state!r}.")
```
Evidence: `agentdeck/authoring/workflow.py:66`, `agentdeck/authoring/workflow.py:90`, `agentdeck/authoring/workflow.py:221`
Ref: https://docs.pydantic.dev/latest/

### The public API could not serve the deck's own operator surface [BAD] (severity: medium)
`serve.py` needs pending runs with their payloads and answer-by-run-id; neither exists publicly, so it calls `deck._pending()` and `deck._answer()`. The public route is `Runs.list(status=WAITING_ANSWER)` plus a `Run.pending()` per handle, and since each of those is itself a full listing, an approval inbox (a first-class use case here) has no first-class API and no non-quadratic one.

```python
        return interrupt_inbox(await deck._pending(), name)
...
        return await deck._answer(paused.run_id, body["value"])
```
Evidence: `agentdeck/serve.py:280`, `agentdeck/serve.py:296`, `agentdeck/deck.py:964`

### SkillError is a public export the library never raises, with a false docstring [BAD] (severity: medium)
It names `SkillExecutionError` and `SkillEnvError` as its subclasses and neither exists anywhere in the repo; nothing under `agentdeck/` raises `SkillError` either, and its only live uses are tests that needed a convenient exception to throw. Skills became disclosure rather than execution, and this error outlived the feature.

```python
class SkillError(AgentdeckError):
    """Base for skill-execution failures (``SkillExecutionError``, ``SkillEnvError``)."""
```
Evidence: `agentdeck/errors.py:32`, `agentdeck/__init__.py:47`

### TurnResult is hand-rolled and mutable, unlike every other value object here [BAD] (severity: low)
`Agent` and `Workflow` go out of their way to be immutable; the object users actually receive is not, and `result.output = ...` succeeds. It also hand-writes `__eq__`, `__repr__`, and `__slots__` that `@dataclass(frozen=True, slots=True)` supplies for free, which is the form the project's own standards specify.

```python
class TurnResult:
    __slots__ = ("output", "run_id", "session_id", "usage")

    def __init__(self, *, output: Any, usage: Usage, run_id: str, session_id: str | None = None) -> None:
        self.output = output
```
Evidence: `agentdeck/deck.py:133`, `CLAUDE.md:42`

### Class docstrings are design documents, not API reference [BAD] (severity: medium)
`help(Deck)` is roughly forty lines of rationale citing internal planning files (`docs/design/run-identity.md`, `plan-phase4-deck.md`) and issue numbers a user cannot read. This is the primary reference surface in every IDE tooltip and it explains why the API is shaped this way before it explains how to call it, against the project's own rule that prose be terse and dense with signal.

```python
    ``observers=`` are the read-only taps on this Deck's event stream  -  telemetry, cost, audit,
    any :class:`~agentdeck.core.ports.EventSinkPort`, and :class:`agentdeck.observers.Langfuse`
    is the one agentdeck ships. Each one's ``start()`` is called once, while the Deck opens,
```
Evidence: `agentdeck/deck.py:348`, `CLAUDE.md:21`

### testing.py stubs one of the two engines [BAD] (severity: medium)
`ScriptedModel` is entirely OpenAI-Agents-shaped, so a workflow-first deck has no public test double for a graph. The `stub` adapter publicly exports `StubEngine`, `Step`, and `stub_spec` with no public way to install any of them: `_engines=` is a private-by-name seam, so the shipped stub is unreachable from the shipped API.

```python
__all__ = ["Step", "StubEngine", "stub_spec"]
```
Evidence: `agentdeck/adapters/engines/stub/__init__.py:5`, `agentdeck/deck.py:405`, `agentdeck/testing.py:48`

### Adapter packages export tuning constants and context keys [BAD] (severity: low)
The top-level namespace is clean; the adapters are not. `MAX_OPEN_CALLS`, `MAX_OPEN_RUNS`, `MCP_SERVER_NAMES_KEY`, `DURABLE_KEY`, and `REPORTER_KEY` are internal plumbing published as package API, which is precisely the leak the project's principles name.

```python
__all__ = ["MAX_OPEN_CALLS", "MAX_OPEN_RUNS", "LangfuseSink", ...]
```
Evidence: `agentdeck/adapters/telemetry/langfuse/__init__.py:8`, `agentdeck/adapters/tools/mcp/__init__.py:11`

### A public docstring documents a method deleted with v1 [BAD] (severity: low)
`AgentNode` tells the reader its deltas surface through `run_workflow_stream`. That method was dropped; the current one is `Workflow.run_stream`. A user following the docstring searches for an API that does not exist.

```python
    stream (``get_stream_writer()``) so ``run_workflow_stream`` surfaces them too.
```
Evidence: `agentdeck/authoring/nodes.py:80`, `docs/delivery/decision-v3-entry-point.md:55`

### Deck takes both `session_factory=` and `_session_factory=` [BAD] (severity: low)
One concept, two constructor parameters, resolved by a precedence line. The private one is now redundant with the public one it predates.

```python
        self._session_factory_arg = session_factory if session_factory is not None else _session_factory
```
Evidence: `agentdeck/deck.py:399`, `agentdeck/deck.py:407`, `agentdeck/deck.py:422`

## Bottom line

The composition and lifecycle design is the strong half and it is genuinely strong: `Context[T]`, the `Run`/`Runs` split, start-versus-observe, and error messages that name the fix are all ahead of what the wrapped SDKs give their users. The typing half has not kept up, and the gap between "declare a pydantic model" and "receive a dict" is the one users will hit on day one, followed by `except AgentdeckError` failing to catch a failed run. Fix the typed-result path, move the two `RuntimeError`s into the hierarchy, and rename or hide `Agent.run`/`Workflow.run`, and this reads like a mature SDK rather than a very good one with three sharp edges.
