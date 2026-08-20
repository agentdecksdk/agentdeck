# 10. Mental Model Consistency

AgentDeck's core vocabulary is unusually disciplined: `core/status.py` is a written constitution for the run lifecycle, and there is a test whose only job is to pin two spellings of the same concept together. The incoherence is not in the middle, it is at the seams. Every place the model wobbles, an engine's native vocabulary is showing through: `turn`, `Runner`, `Session` and `MAX_TURNS` from the OpenAI Agents SDK, and `thread_id`, `checkpoint` and resume-means-answer from LangGraph.

## The intended model

You declare Agents, Workflows and Skills; a Deck is the catalog that holds them and the only thing that starts them. Starting one produces a Run: a first-class object with a minted `id`, an optional application `key`, an isolating `namespace`, and an optional `session_id` naming the conversation it belongs to. Everything that happens goes into one ordered append-only event log, and a run's status is folded from that log rather than stored beside it, so there is never a second source to disagree. A run is in exactly one of six states; three are terminal, and the two suspended ones are distinguished by how they continue: `paused` is lifted with `resume()`, `waiting_answer` is satisfied with `answer(value)`, and conflating them is an error the SDK refuses by name. Pause and cancel are requests honored at safe points, not preemptions. Namespace isolates, key identifies, session accumulates, and the log is the truth.

That paragraph is a fair reading of the code. Most of what follows is where the codebase and the docs say something else.

---

### The lifecycle is one table, written once, and it fails at import if incomplete [GOOD] (severity: high)
`core/status.py` is the single place any lifecycle rule may live: four declarations covering what is true of a state, what moves it, what is legal in it, and what a pending signal does. Nothing is spelled twice, and a new state with no row raises `KeyError` while the module is importing.
```python
TERMINAL_STATUSES = frozenset(TRANSITIONS[kind] for kind in TERMINAL_KINDS)
RESUMABLE_STATUSES = frozenset(status for status, facts in STATES.items() if facts.suspended)
SUSPENDED_KINDS = frozenset(kind for kind, status in TRANSITIONS.items() if STATES[status].suspended)
POLICY: Mapping[tuple[RunStatus, str | None], Ruling] = {
    (status, verb): _ROUTING[status][verb] for status in RunStatus for verb in _RUNNING_ROW
}
```
Evidence: `agentdeck/core/status.py:97-128`

### The SDK tests its own vocabulary for drift [GOOD] (severity: high)
Three value sets are deliberately written twice (an enum a caller branches on, a Literal the schema validates). A dedicated test file pins each pair, so adding a member to one and forgetting the other is a red build rather than a silent hole.
```python
def test_invocable_kinds_match_what_run_started_accepts():
    literal = get_args(RunStarted.model_fields["kind_of_invocable"].annotation)
    assert {kind.value for kind in InvocableKind} == set(literal)
```
Evidence: `tests/core/test_vocabularies_agree.py:1-46`

### A user can predict the whole lifecycle from one docs page [GOOD] (severity: high)
`lifecycle-and-control.mdx` gives the complete state table, the complete event-to-status table, which states are resumable, and which verb moves between which. Nobody has to read `core/status.py` to know what exists. It even pre-empts the obvious wrong guess ("There is no queued state") and states the safe-point semantics of pause.
```markdown
| `run.started` | `running` |
| `run.paused` | `paused` |
| `run.interrupted` | `waiting_answer` |
...
`pause` and `cancel` are requests, not interrupts.
```
Evidence: `docs-site/content/runs-and-control/lifecycle-and-control.mdx:34-67`

### Refusals name the verb that would have worked [GOOD] (severity: high)
The precondition table separates what a caller *invokes* from what is *pending*, and every refusal is written to be read by the person refused. This is the single strongest piece of vocabulary teaching in the codebase: the error itself carries the model.
```python
RunStatus.PAUSED: {
    Operation.ANSWER: Precondition(
        Verdict.REFUSED, "the run is paused, not waiting for a value: lift it with run.resume()"
    ),
```
Evidence: `agentdeck/core/status.py:150-166`

### The four identity concepts are named in one table, with their roles [GOOD] (severity: high)
The Runs page does the thing most SDKs never do: it says what each identity value is *for*, not just that it exists. `id` is minted, `key` is yours, `namespace` is a label, `session_id` is the conversation.
```markdown
| `run.id` | Minted by AgentDeck, globally unique |
| `run.key` | The application identity you chose, if you passed one |
| `run.namespace` | The label this run was started under |
| `run.session_id` | The conversation it belongs to, if any |
```
Evidence: `docs-site/content/runs-and-control/runs.mdx:22-27`

### Minting the run id, and recording why deriving it was retired [GOOD] (severity: medium)
`RunContext.id` is a plain read of `run_id`, not a computation, and the docstring records the derivation it replaced and why. This is what keeps "the run's address" from becoming a fifth identity concept.
```python
@property
def id(self) -> str:
    """This run's durable address  -  what the control plane addresses it by, everywhere.
    A carried value, not a computed one: a plain read of :attr:`run_id` ..."""
    return self.run_id
```
Evidence: `agentdeck/core/context.py:106-118`

### `Context[T]` is one portable noun above two engines [GOOD] (severity: medium)
The OpenAI SDK hands a tool a `RunContextWrapper` and LangGraph hands a node a `Runtime`. Each bridge unwraps its own carrier and presents the same `Context`, so a tool signature does not change when the engine does. The docstring states exactly which fields are deliberately absent and why.
```python
class Context[T]:
    """The only public context type: what a user callable declaring ``Context[T]`` receives.
    One portable type above two engines. ..."""
```
Evidence: `agentdeck/core/context.py:121-146`

### `namespace` is defined by what it is not [GOOD] (severity: medium)
Both the code and the README refuse to let `namespace` grow into a tenancy or auth concept, in the same words. A user cannot come away thinking it authenticates anyone.
```python
"""``namespace`` is an opaque isolation boundary and nothing more. AgentDeck never parses it,
never compares its parts, and attaches no meaning to it ... It says which runs are kept
apart, never who is acting or what they may do."""
```
Evidence: `agentdeck/core/context.py:29-34`, `README.md:195`

### The empty namespace is refused, with the encoding reason in the message [GOOD] (severity: medium)
`""` and `None` would collide in every store, so the value object rejects the ambiguous one and says why. The invariant is enforced where the concept lives, not in four adapters.
```python
raise ValueError(
    "namespace must be a non-empty string or None; empty is how stores encode "
    "'no namespace', so an explicit '' would share a bucket with unnamespaced runs"
)
```
Evidence: `agentdeck/core/context.py:88-91`

### The README's ownership table is the model, stated as a contract [GOOD] (severity: medium)
Six rows of "you own X, AgentDeck owns Y" is a better teaching device than a primitives list, because it tells the reader where to stop building. It also maps cleanly onto the ring structure the architecture actually has.
```markdown
| You own | AgentDeck owns |
| what progress means | **Reporting.** Progress and status, sent from inside the work. |
| when work should stop | **Control.** Execution paused, resumed or cancelled at safe points. |
| when a person decides | **Interaction.** Branches that wait for external input. |
```
Evidence: `README.md:157-168`

---

### Six words for four identity concepts, and only four are documented [BAD] (severity: high)
`run_id`, `key`, `namespace` and `session_id` are the taught model. `log_key` and `thread_id` are two more values in the same space that a user meets anyway: `log_key` in every `SessionBusyError`, `thread_id` in every `InterruptResult` and in an HTTP query parameter. Neither appears in any docs-site page.
```python
@property
def log_key(self) -> str:
    """Where this run's events are written  -  a run without a session is its own log,
    so persist-before-yield holds for it too."""
    return self.session_id or self.run_id
```
Evidence: `agentdeck/core/context.py:99-104`

### Run and session share one value space, so rehydration has to guess which it has [BAD] (severity: medium)
Because `log_key` is `session_id or run_id`, a stored run gives no way to tell "session named X" from "no session, run id X". `Runs.get` recovers `session_id` by string-comparing the two, which silently returns `None` for anyone who passed `session_id=` equal to a run id.
```python
session_id = None if summary.log_key == summary.run_id else summary.log_key
```
Evidence: `agentdeck/deck.py:1383`

### `SessionBusyError` calls a run a session [BAD] (severity: medium)
For an unsessioned run `log_key` *is* the run id, so the error reads "session '<uuid>' already has run '<uuid>' in flight" about a run that has no session at all. The one error most likely to be a user's first encounter with the word "session" teaches the wrong relation.
```python
return (
    f"session {ctx.log_key!r} already has run {held_by!r} in flight, "
    f"so run {ctx.run_id!r} cannot start on it  -  see {_SESSIONS_DOCS}"
)
```
Evidence: `agentdeck/runtime/service.py:650-653`

### `run.key` is `None` on every handle you did not start yourself [BAD] (severity: medium)
The docs call `key` "the application identity you chose", and `Runs.get(id)` and `Runs.list()` both hard-code `key=None` regardless of what the run actually claimed. So `run.key is None` does not mean the run has no key, and the store's `(namespace, key)` claim is unreadable from the handle the SDK hands back.
```python
Run(self._deck, id=summary.run_id, key=None, namespace=namespace, ...)
```
Evidence: `agentdeck/deck.py:1399-1407`, `agentdeck/deck.py:1385`

### The HTTP surface calls a session a thread [BAD] (severity: medium)
`POST /workflows/{name}?thread_id=...` passes that value straight through as `session_id`. The comment says so out loud. A user reading the Sessions page and then the HTTP docs meets two names for one thing, with LangGraph's name winning on the wire.
```python
async def run_workflow(name: str, state: dict[str, Any], stream: bool = False, thread_id: str | None = None) -> Any:
    # The posted state *is* the graph's input, and the thread the caller named is the
    # session it runs under: one turn per thread at a time, and a resume can find it later.
    run = deck.stream(name, state, session_id=thread_id)
```
Evidence: `agentdeck/serve.py:211-219`

### `Deck.session_for()` returns a different kind of session than `session_id` names [BAD] (severity: medium)
A public method on the composition root, whose return type is the OpenAI Agents SDK's own `Session` protocol, whose defining module says nothing outside that adapter may import it. So the deck has two "sessions": the event-log key the user passes in, and the engine's conversation memory object the user gets back, and only one of them is a type they can name.
```python
def session_for(self, session_id: str) -> Session:
    """Conversation memory for ``session_id``  -  the engine's own store, so a turn started
    here and one started over HTTP land in the same conversation."""
    return self._ensure_sessions().session_for(_new_context(session_id))
```
Evidence: `agentdeck/deck.py:1095-1098`, `agentdeck/adapters/engines/openai_agents/sessions.py:9`

### One user-facing noun, three separately named backends [BAD] (severity: medium)
"Session" is taught as one thing that "maintains conversation history and state across multiple runs". Behind it are three independently configured stores, and the word `SESSION` names only one of them. Point `AGENTDECK_EVENTS` at Postgres and forget `AGENTDECK_SESSION` and the log outlives the memory, with nothing anywhere saying they are two halves of one concept.
```text
AGENTDECK_EVENTS      # the canonical event log
AGENTDECK_SESSION     # the Agents SDK's conversation memory
AGENTDECK_CHECKPOINT  # LangGraph's checkpointer
```
Evidence: `docs-site/content/reference/settings.mdx:50-56`, `docs-site/content/runs-and-control/sessions.mdx:5`

### `resume` means two different mechanisms depending on which suspension you are in [BAD] (severity: high)
Lifting a pause calls `engine.start` and replays the turn from its original input with the log as history, so tools already executed run again. Answering an interrupt calls `engine.resume` and re-enters at the checkpoint. One verb, at-least-once on one branch and exactly-once on the other, and the only statement of it is a docstring inside `runtime/service.py`. The docs-site page that should carry this is a ten-line stub whose subtitle is "resume them safely".
```python
history = await self._store.read(summary.log_key, run_ctx)
stream = engine.start(spec, opened.input, history, run_ctx)
```
Evidence: `agentdeck/runtime/service.py:341-342`, `docs-site/content/runs-and-control/pause-resume.mdx:1-10`

### Two HTTP routes named `resume`, doing the two things core refuses to conflate [BAD] (severity: high)
`POST /runs/{run_id}/resume` lifts a pause. `POST /workflows/{name}/{thread_id}/resume` takes a `value` and calls `_answer`. `status.py` spends an entire table making sure a caller cannot mistake one for the other, and the wire hands both the same verb, because LangGraph spells answering `Command(resume=value)`.
```python
@api.post("/workflows/{name}/{thread_id}/resume")
async def resume_workflow(name: str, thread_id: str, body: dict[str, Any]) -> Any:
    ...
    return await deck._answer(paused.run_id, body["value"])
```
Evidence: `agentdeck/serve.py:282-296`
Ref: https://langchain-ai.github.io/langgraph/

### The workflow-resume 404 calls a waiting run paused [BAD] (severity: medium)
The lookup filters `_pending()`, which lists `WAITING_ANSWER` runs only, then reports the miss as "no paused run". `PRECONDITIONS` refuses `answer` on a `PAUSED` run with a message explaining they are not the same thing; this error message undoes that lesson on the surface most users hit first.
```python
raise NotFoundError(f"No paused run of {name!r} on thread {thread_id!r}.")
```
Evidence: `agentdeck/serve.py:295`

### `run.answer()` is taught as universal and is unreachable for every agent [BAD] (severity: high)
`RunInterrupted` has exactly one producer, in the LangGraph engine. `_answer` searches `runtime.pending()`, which lists only `WAITING_ANSWER` runs, so on an agent it always raises `NotFoundError`. Both the README and the lifecycle page demonstrate `answer` on Jack, who is an `Agent`.
```markdown
run = await deck.runs.start("Jack", question)
await run.answer(value)    # waiting_answer -> running
```
Evidence: `docs-site/content/runs-and-control/lifecycle-and-control.mdx:52-58`, `agentdeck/adapters/engines/langgraph/engine.py:419`, `agentdeck/deck.py:969-975`

### The CLI knows three of the four control verbs [BAD] (severity: medium)
`agentdeck runs signal` accepts `cancel`, `pause`, `resume` and not `answer`. From the CLI, a run parked on an interrupt can only be killed. The verb pairing the lifecycle page carefully teaches has no operator surface for half of itself.
```text
usage: agentdeck runs signal [-h] --control-db CONTROL_DB [--reason REASON]
                             run_id {cancel,pause,resume}
```
Evidence: `docs-site/content/reference/cli.mdx:32-49`

### The CLI speaks in ports, not in the product's nouns [BAD] (severity: medium)
The only command a user can run takes `--control-db`, a raw path to the `ControlPort`'s SQLite file. There is no `Deck`, no project, no session and no agent anywhere in the CLI's model: it addresses the machinery the rest of the SDK exists to hide.
```text
  --control-db CONTROL_DB
                        path to the ControlPort's SQLite file
```
Evidence: `docs-site/content/reference/cli.mdx:45-46`

### `steer` is in the event vocabulary with no way to cause it [BAD] (severity: low)
`ControlVerb` has four members, `Signal` has three, and a test asserts the gap is exactly `steer`. So a reader of the event schema learns a control verb no public or private API can produce, and the events reference calls `input.appended` "mid-turn steering" without connecting the two.
```python
assert set(get_args(ControlVerb)) - {signal.value for signal in Signal} == {"steer"}
```
Evidence: `tests/core/test_vocabularies_agree.py:35-38`, `agentdeck/core/events.py:178`

### The schema lets `run.interrupted` carry `reason="pause"` [BAD] (severity: low)
`reason` is read out of the interrupt payload a user's node supplies and accepted if it is in `_KNOWN_REASONS`, which includes `"pause"`. A node can therefore mint an event that says "paused" and puts the run in `WAITING_ANSWER`, which is the one conflation the whole status module is built to prevent.
```python
reason = value.get("reason") if isinstance(value, Mapping) else None
return RunInterrupted(reason=reason if reason in _KNOWN_REASONS else "human", ...)
```
Evidence: `agentdeck/adapters/engines/langgraph/engine.py:416-424`, `agentdeck/core/events.py:172`

### One suspended state, two return shapes, and a named error used by neither [BAD] (severity: high)
`await deck.run(...)` on a run that suspends raises a bare `RuntimeError` for an agent and returns an `InterruptResult` dict for a workflow. `RunSuspendedError` exists in the taxonomy for exactly this situation and neither branch raises it. Same verb, same state, three different answers depending on which kind you asked for and which entry point you used.
```python
if result is None:
    raise RuntimeError(
        "the run ended without completing (paused or cancelled)  -  resume it with "
        "(await deck.runs.get(run_id)).resume(), or inspect the event log for what happened."
    )
```
Evidence: `agentdeck/deck.py:220-225`, `agentdeck/deck.py:243-245`, `agentdeck/errors.py:91`

### Skill carries two incompatible models at once [BAD] (severity: medium)
In `authoring/skills.py` a skill is disclosure: instructions text plus one `load_skill` tool, never executed by the runtime. In `core/` a skill is a third thing the Runtime can start, and `SkillError` documents execution subclasses that do not exist. `Deck._root` resolves agents and workflows only, so the invocable kind has no producer at all.
```python
class InvocableKind(StrEnum):
    AGENT = "agent"
    WORKFLOW = "workflow"
    SKILL = "skill"
```
Evidence: `agentdeck/core/invocable.py:1-20`, `agentdeck/errors.py:32-33`, `agentdeck/deck.py:791-798`

### "Turn" has three meanings and is never defined [BAD] (severity: medium)
`TurnResult` is one run's outcome. The reference page uses "turn" for a conversational exchange and contrasts it with "workflow run". `AGENTDECK_RUNNER_MAX_TURNS` means iterations of the model loop inside a single run. No docs page defines the word, and the only public type carrying it is the return value of the main path.
```text
| `AGENTDECK_RUNNER_MAX_TURNS` | `int` | `30` | Maximum turns `Runner.run`/`run_streamed` may take before giving up. |
```
Evidence: `docs-site/content/reference/settings.mdx:29`, `docs-site/content/reference/deck.mdx:478-479`, `agentdeck/deck.py:133`
Ref: https://github.com/openai/openai-agents-python

### A settings namespace built entirely out of another SDK's nouns [BAD] (severity: medium)
`AGENTDECK_RUNNER_*` uses "Runner", a class name from the Agents SDK that appears nowhere in AgentDeck's own model, and `AGENTDECK_RUNNER_WORKFLOW_NAME` spends the core noun `Workflow` on an unrelated tracing label. A user with a declared `Workflow` who sets it changes nothing about that workflow, and these six settings silently apply to one engine of two.
```text
| `AGENTDECK_RUNNER_WORKFLOW_NAME` | `str` | `'agentdeck'` | Name recorded on the host Agents SDK run (`RunConfig.workflow_name`) ... |
```
Evidence: `docs-site/content/reference/settings.mdx:20-31`

### `context=` is a type in one place and an instance in the other [BAD] (severity: medium)
`Deck(context=DocsCorpus)` declares the type; `deck.run(context=corpus)` supplies the instance. Both parameters are annotated `object`, so swapping them is caught by neither the checker nor the constructor. The README states the distinction in one sentence and then relies on the reader holding it.
```python
deck = Deck(agents=[jack], context=DocsCorpus)
# `context=DocsCorpus` is the *type*; the instance goes in per run.
```
Evidence: `README.md:74-76`, `agentdeck/deck.py:397`, `agentdeck/deck.py:874`

### The page titled "Mental Model" is fifteen lines and gets the Run/Session relation backwards [BAD] (severity: high)
It lists four primitives, and defines a Run as "an executing instance with an isolated session". A session is not owned by a run: it spans many, one at a time, which is the entire point of `SessionBusyError` and of the Sessions page. The one page whose whole job is the conceptual model contradicts it in its last bullet.
```markdown
- **Run:** An executing instance with an isolated session and immutable event stream.
```
Evidence: `docs-site/content/meet-agentdeck/mental-model.mdx:15`

### The Overview says a Run owns exactly what the code says it does not [BAD] (severity: medium)
Overview: a Run "manages lifecycle, storage, and event dispatch". `Run`'s own docstring: it holds no engine, store, MCP registry or observer, and if it ever grows one the design is wrong. Both are read on day one.
```python
"""A deck-bound handle on one run  -  not a second runtime. It holds no engine, store, MCP
registry or observer, and delegates every operation back through the deck's own
infrastructure ...; if it ever grows one of those, the design is wrong."""
```
Evidence: `docs-site/content/meet-agentdeck/overview.mdx:27`, `agentdeck/deck.py:1133-1136`

### Three taxonomies of the same system in the three most-read places, and Session is in none [BAD] (severity: medium)
The README teaches six machinery nouns (Events, Reporting, Control, Interaction, State, Surfaces). The architecture diagram draws eight (Deck, Agents, Workflows, Tools, Skills, Run, Events, Control). The Mental Model page names four. No two agree, and the noun a conversational app must learn on its first day appears in none of them.
```tsx
>Deck<  >Agents<  >Workflows<  >Tools<  >Skills<  >Run<  >Events<  >Control<
```
Evidence: `docs-site/app/diagram.tsx`, `README.md:157-168`, `docs-site/content/meet-agentdeck/mental-model.mdx:11-15`

### The docs name a state the enum does not have [BAD] (severity: medium)
`RunStatus` has `WAITING_ANSWER`. The Human Input page tells the reader to act "when a run enters `WAITING`", in backticks, as if quoting the API. A user grepping for it finds nothing, and the state it half-names is the one the entire pause-versus-answer distinction hangs on.
```markdown
When a run enters `WAITING`, supply input with `run.answer()`:
```
Evidence: `docs-site/content/runs-and-control/human-input.mdx:5`

### The state vocabulary is only importable from the internal ring [BAD] (severity: medium)
`RunStatus` is the return type of `run.status()`, a parameter of `deck.runs.list()`, and appears in six docs snippets. It is not in `agentdeck.__all__`, so the one import the docs actually show reaches into `agentdeck.core.status`, the ring the architecture doc declares internal.
```python
from agentdeck.core.status import RunStatus
```
Evidence: `docs-site/content/runs-and-control/lifecycle-and-control.mdx:25`, `agentdeck/__init__.py:37-51`

### "The path is the registration" covers three of the five authoring nouns [BAD] (severity: low)
`.agentdeck/` has `agents/`, `workflows/` and `skills/`, and discovery walks exactly those. Tools and MCP servers are Python-only, declared on an `Agent`. The claim is a strong one and mostly true, but a reader who takes it literally will look for `.agentdeck/tools/`.
```python
registry = self._discover(Agent, type_dir="agents", module_name="agent", label="agent")
registry = self._discover(Workflow, type_dir="workflows", module_name="workflow", label="workflow")
```
Evidence: `agentdeck/runtime/discovery.py:102-106`, `README.md:129-136`

---

## Bottom line

The model AgentDeck built for itself is better than most SDKs manage: one lifecycle table that cannot drift, a test that pins duplicated vocabularies, and refusal messages that teach the distinction they are enforcing. What it has not done is defend that model at its borders, so LangGraph's `thread_id` and its fused resume-means-answer come back in through the HTTP surface, and the Agents SDK's `Runner`, `Session` and `turn` come back in through the settings and the main return type. The single highest-value repair is not new machinery: it is deciding that the six documented states and their four verbs are true on both engines and on all three surfaces, then fixing the words, the CLI verb set, and the fifteen-line page that currently carries the title "Mental Model".
