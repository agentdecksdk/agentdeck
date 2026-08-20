# Test Quality Audit: AgentDeck SDK

21K LOC, 1025 test functions, four conftest files, zero mocks. This is one of the better-engineered Python test suites I have read: the contract suite is a real conformance matrix, the race tests synchronize through files instead of hoping, and the goldens are reviewable text with orphan pruning. The damage is concentrated in three places: an untested MCP resilience path, a schema forward-compat guardrail that has been switched off for two major releases, and a 2130-line `test_deck.py` that is the flat layout's bill coming due.

The scout's five-item coverage-gap list verified to two real gaps, one dead export, and two false positives. That correction is in the findings below.

## Findings

### Contract suite is one parametrized spine, not per-adapter copies [GOOD] (severity: high)
Engine cases live in one flat list; adding an engine appends cases and every invariant in `test_event_stream.py`, `test_resume.py`, `test_control.py` and `test_context_parity.py` runs against it unchanged. This is exactly what testing.md section 2 asks for and almost nobody actually builds.
```python
CASES: list[Case] = _stub_cases() + openai_agents_cases() + langgraph_cases()
```
The one asymmetry is defensible: the misbehaving-engine cases (`stops-without-a-terminal-event`, `yields-after-a-terminal-event`) run only against the stub, because they assert Runtime-level repair, and the openai-agents engine has no suspend primitive to fill the `ends="suspended"` cell that `test_resume.py` needs.
Evidence: `tests/contract/contract_cases.py:104`, `tests/contract/case_types.py:20`

### Store contract runs against real Redis and Postgres, with disjoint keyspaces per case [GOOD] (severity: high)
All four backends run the same 56 cases; Redis gets a fresh key prefix and Postgres a fresh schema, both seeded with the pid so two `make check` runs on one host cannot collide. A store that only answers correctly in a mock is not evidence, and this suite says so and then acts on it.
```python
BACKENDS = ("memory", "sqlite", "redis", "postgres")
_run = f"{os.getpid():x}"
_names = count()
```
Evidence: `tests/contract/live_stores.py:38`, `tests/contract/live_stores.py:67`, `tests/contract/test_store.py:69`

### CI hunts silent skips by reason, not by count [GOOD] (severity: high)
Two separate guards: an importability probe for every extra the suite conditions on, and a grep over the JUnit XML for skip reasons that mean "a subsystem dropped out". Counting skips drifts as the matrix grows; matching reasons does not. This is the correct fix to the classic green-gate-that-measured-nothing bug (#33, #142).
```python
if grep -qE 'needs the \[[a-z]+\] extra|no Postgres at|no Redis at' /tmp/pytest-results.xml; then
  grep -oE 'needs the \[[a-z]+\] extra|no (Postgres|Redis) at [^"]*' /tmp/pytest-results.xml | sort -u
  exit 1
fi
```
Evidence: `.github/workflows/ci.yml:98`, `.github/workflows/ci.yml:82`

### Goldens are reviewable text, and orphans cannot survive a rename [GOOD] (severity: medium)
39 snapshots across two dirs, all `.http` and pretty-printed `.json`, so a diff reads as a schema change rather than a blob swap. The set comparison runs before the byte comparison, so a missing or orphaned kind reads as a set diff instead of a `FileNotFoundError`.
```python
    if UPDATE:
        _record_into(SNAPSHOTS, recorded)
        return
    assert sorted(recorded) == sorted(p.name for p in SNAPSHOTS.glob("*.json"))
    for name, body in recorded.items():
        assert body == (SNAPSHOTS / name).read_bytes(), f"schema changed: {name}"
```
Evidence: `tests/core/test_golden_json.py:33`, `tests/core/snapshots/run.completed.json:1`

### The snapshot fixture is itself pinned against index drift [GOOD] (severity: medium)
Adding a payload kind must rewrite exactly one file. The test prepends the newcomer rather than appending, because appending is the one insertion that passes under the regression it guards, and it uses the fixture's own builder rather than re-deriving `seq`. That is a test author who thought about how their own test could lie.
```python
    changed = {
        path.name
        for path in after.glob("*.json")
        if not (before / path.name).exists() or (before / path.name).read_bytes() != path.read_bytes()
    }
    assert changed == {"test.newcomer.json"}, changed
```
Evidence: `tests/core/test_golden_json.py:43`

### Race tests arrange contention through files and assert one winner [GOOD] (severity: high)
Six races across two real OS processes over one SQLite file. Peers rendezvous on files rather than sleeps, timing-decided races repeat many trials, every subprocess has a timeout, and the assertion is the promised outcome. Testing.md section 7's example is written in this file verbatim.
```python
        assert len(winners) == 1, f"trial {trial}: {winners or 'nobody'} won\n{_dump(log)}"
        assert yielded[winners[0]] == worker.APPROVED_KINDS, f"trial {trial}: {yielded}\n{_dump(log)}"
        assert marks.count(real_id) == 1, (
            f"trial {trial}: the engine played node B {marks.count(real_id)} times\n{_dump(log)}"
        )
```
Which side wins a genuine coin toss is printed, not asserted. Correct.
Evidence: `tests/test_multiprocess_concurrency.py:228`, `tests/test_multiprocess_concurrency.py:173`

### The staleness window is measured from stored stamps, not from the sleep [GOOD] (severity: medium)
The `AGE_GAP = 0.5` sleeps in the store contract are not a flakiness source. The window is derived from the two events' own `ts` values at 0.9x, so a scheduling stall between the writes widens the margin instead of eating it. This is the single most common way a time-based store test flakes, and it is closed.
```python
def _window_between(older: Event, newer: Event) -> timedelta:
    return (newer.ts - older.ts) * 0.9
```
Evidence: `tests/contract/test_store.py:203`, `tests/contract/test_store.py:190`

### Zero mocks in 21K LOC [GOOD] (severity: high)
No `MagicMock`, no `AsyncMock`, no `unittest.mock` anywhere. 44 `monkeypatch.setattr` calls total, and 19 private-attribute assertions, every one of them annotated as deliberate wiring rather than behavior. 224 `pytest.raises`. Suites this size normally assert on call recorders; this one asserts on outcomes.
```python
assert factory._key_prefix == "p"  # noqa: SLF001  -  asserting the constructor wiring, not behavior
assert factory._ttl == 60  # noqa: SLF001
```
Evidence: `tests/test_openai_agents_sessions.py:28`

### 1025 test names, none of them vague [GOOD] (severity: medium)
Not a single test name reduces to three or fewer words. Names state the guarantee, including the awkward long ones, which is the right trade.
```python
def test_a_session_a_killed_run_left_open_is_refused_until_the_staleness_window_passes(tmp_path: Path) -> None:
def test_staleness_is_measured_from_the_last_event_of_a_run_not_its_last_transition(...) -> None:
def test_close_does_not_swallow_a_cancellation_aimed_at_its_caller() -> None:
```
Evidence: `tests/test_multiprocess_concurrency.py:405`, `tests/contract/test_store.py:310`

### Documentation fences execute as real subprocesses against a scripted HTTP model [GOOD] (severity: medium)
A `run` fence is written into a temp project and executed as `python <script>` against a scripted OpenAI-compatible HTTP server, patching nothing in `agentdeck`. An `illustrative reason="..."` token opts a fence out on purpose instead of leaving it silently unexecuted. Docs rot is a real defect class and this is a real gate on it, not a link checker.
```python
_SUBPROCESS_TIMEOUT = 30
```
Evidence: `tests/test_docs_examples.py:1`, `tests/conftest.py:9`

### A store double that hands the loop no turn at all [GOOD] (severity: medium)
`NeverYields` strips out even `MemoryEventStore`'s single suspension point, so liveness-sensitive callers are tested against the one scheduling profile no deployment provides and every caller must survive anyway. Its sink counterpart `Unyielding` does the same on the other side. This is the kind of double you only write after being burned.
```python
class NeverYields(EventStorePort):
    def __init__(self, inner: EventStorePort) -> None:
```
Evidence: `tests/contract/never_yields.py:16`, `tests/test_sink_dispatch.py:101`

### No conftest sprawl: four lean files, each with one job [GOOD] (severity: low)
The premise inverts. `tests/conftest.py` is 30 lines. `tests/contract/conftest.py` is the five-fixture parametrization spine and nothing else. `tests/core/conftest.py` holds the event-example factory; `tests/golden/conftest.py` holds the app client and the pinned env. Fixtures live next to the tests that need them instead of in a shared attic.
```python
@pytest.fixture
def store() -> MemoryEventStore:
    """The frozen clock lives here: the store stamps every ``ts`` now (ADR-D11), so this is the
    only seam left through which a case can hold time still."""
    return MemoryEventStore(clock=lambda: TS)
```
Evidence: `tests/contract/conftest.py:32`, `tests/conftest.py:1`

### MCP transport resilience is 234 LOC of failure-path code with zero tests [BAD] (severity: high)
`transport.py` is the hardened `MCPServerStreamableHttp`: connect retry with backoff, mid-session 404 detection, transparent re-`initialize` plus replay, and a transient-versus-fatal status classifier. Every test that touches it monkeypatches `connect` and `cleanup` on the resilient class itself, so none of that logic ever runs. An MCP server restart that breaks the session-lost path, or a status code slipping between `FATAL_STATUS_CODES` and `SESSION_LOST_STATUS_CODES`, ships green.
```python
FATAL_STATUS_CODES: frozenset[int] = frozenset({401, 403, 404})
# A mid-session 404 means a dropped session id (restart), not a bad URL.
SESSION_LOST_STATUS_CODES: frozenset[int] = frozenset({404})
```
Evidence: `agentdeck/adapters/tools/mcp/transport.py:57`, `tests/test_mcp_tool_source.py:80`, `tests/test_deck.py:78`

### The schema forward-compat guardrail is off, and its own re-enable condition expired two majors ago [BAD] (severity: high)
`test_old_reader_compat.py` exists because a schema change was called additive by argument and shipped a break (#107). It is module-skipped, and the skip reason names its own exit condition: re-enable once a release carries the object-shaped `v`. `v3.0.0` carries `SchemaVersion` and the tree is at 4.0.5. The one test written to catch a #107-class break has been dark across two major releases.
```python
pytestmark = pytest.mark.skip(
    reason="envelope v2 removed the required `tenant` for `namespace` and dropped "
    "RunContextSnapshot ... Re-enable with BASELINE moved to the first release that carries the "
    "object `v`."
)
BASELINE = "v2.0.0b4"
```
Evidence: `tests/core/test_old_reader_compat.py:38`, `agentdeck/core/events.py` at tag `v3.0.0:55`

### The compat test that does run measures against a reader two majors stale [BAD] (severity: medium)
`test_old_reader_block_compat.py` is not skipped, but its `BASELINE` is the same `v2.0.0b4`, and its own docstring says to bump it at a release, deliberately. It was not bumped across v3.0.0, v3.1.0, or v4.0.0. It therefore proves that a two-year-old reader tolerates the block type from #109, and says nothing about the reader anybody is actually running. The 30-line `_module_from` git-show helper is also duplicated verbatim between the two files.
```python
BASELINE = "v2.0.0b4"
"""The newest released reader. Bump it at a release, deliberately, never to make a test pass."""
```
Evidence: `tests/core/test_old_reader_block_compat.py:34`, `tests/core/test_old_reader_compat.py:49`

### The direct-call workflow API has no tests at all [BAD] (severity: medium)
`Workflow.run()`, `Workflow.run_stream()` and `Workflow.resume()` route through `DevWorkflowRunner`, 99 LOC that no test names and no test reaches: zero hits for `DevWorkflowRunner` or `BaseWorkflowRunner` outside `agentdeck/`, and no `run_stream` call on a `Workflow` anywhere in `tests/`, `docs/`, or `examples/`. The sibling agent runner is imported and driven directly by `test_streaming.py` and `test_run_config_parity.py`, so this is an omission, not a policy.
```python
    def _runner(self, **runner_options: Any) -> Any:
        from agentdeck.authoring.runners.workflow import DevWorkflowRunner
        return DevWorkflowRunner.from_workflow(self, **runner_options)
```
Evidence: `agentdeck/authoring/workflow.py:107`, `agentdeck/authoring/runners/workflow.py:1`

### test_deck.py is 2130 lines and 100 tests across 22 subjects [BAD] (severity: medium)
This is the flat-layout answer. The file is organized by banner comment, not by module: Deck construction, one-deck-per-process, tool compilation, MCP ownership, ASGI lifespan, the run-identity test matrix, control-port reads, and the cron sweep all live in one file. Navigation is `-k` only, every Deck change collides in the same file, and its opening docstring anchors to an issue's "Done when" list rather than to a subject, which ages badly.
```python
"""``Deck``: the v3 composition root. One test per "Done when" item in #164's 4d slice  -
``Deck.asgi()`` and the golden-wire invariants are covered in 4e; this file is the Python API.
"""
```
70 flat files works today because names are disciplined (`test_langgraph_*`, `test_serve*`, `test_uc*`). It stops working at the point one subject needs four files, which `test_deck.py` and `test_runtime_service.py` (1373 lines) have already reached.
Evidence: `tests/test_deck.py:1`, `tests/test_runtime_service.py:1`

### Two 0.5s wall sleeps with a 100ms margin over cancellation-proof work [BAD] (severity: medium)
The file's own docstring says every dispatch whose behavior turns on time gets a clock the test moves, and it keeps that promise everywhere except here. `Stubborn(slices=20)` performs 0.4s of `CancelledError`-suppressing sleeps; the test then waits 0.5s and asserts the task count is back to baseline. On a loaded CI runner that 100ms is not a margin, and the failure mode is an unrelated-looking task-count assertion.
```python
    sink = Stubborn(slices=20)  # 0.4s of cancellation-proof work, against a 0.2s caller deadline
    ...
    await asyncio.sleep(0.5)
    assert len(asyncio.all_tasks()) == before  # the abandoned consumer retires itself, not leaks
```
The fix is a bounded poll on the condition, not a longer sleep.
Evidence: `tests/test_sink_dispatch.py:826`, `tests/test_sink_dispatch.py:1030`, `tests/test_sink_dispatch.py:251`

### FakeClock is implemented twice, differing only in an attribute name [BAD] (severity: low)
Two independent copies, 12 lines each, identical behavior; one calls the field `seconds` and starts at 0.0, the other calls it `now` and starts at 1000.0. `tests/` already has a convention for shared helpers (`event_log_checks.py`, `never_yields.py`, `project_engines.py`), so there is a home for it. Low impact, but it is the seam through which two suites can drift on what "advance" means.
```python
class FakeClock:
    """Monotonic seconds the test moves by hand, so a cooldown is asserted rather than waited out."""
    def __init__(self) -> None:
        self.seconds = 0.0
```
Evidence: `tests/test_sink_dispatch.py:26`, `tests/test_run_control.py:64`

### An exported env var lets `make check` rewrite the baselines it is checking [BAD] (severity: low)
`AGENTDECK_GOLDEN_UPDATE=1` in a developer's shell turns both golden suites into recorders, and the gate passes having written the answer it was asked for. Nothing asserts the variable is unset outside `make golden`, and no step diffs the working tree after `test`. Testing.md section 3 forbids exactly this, and the current defense is code review noticing 39 rewritten snapshots in a diff.
```python
UPDATE = os.getenv("AGENTDECK_GOLDEN_UPDATE") == "1"
```
One line in the golden tests (`assert not UPDATE` when `CI` is set) or a `git diff --exit-code tests/*/snapshots` step closes it.
Evidence: `tests/core/test_golden_json.py:17`, `tests/golden/test_golden_wire.py:13`, `Makefile:32`

### Pytest config carries no warning gate and no upper bound on the async plugin [BAD] (severity: low)
The whole config is `asyncio_mode` and `pythonpath`. No `filterwarnings = ["error"]` on a harness whose entire value proposition rides two fast-moving external SDKs, so an OpenAI Agents or LangGraph deprecation lands as scrollback rather than as a failing build, and the suite runs `-q`. `pytest-asyncio>=0.23` with no ceiling and no `asyncio_default_fixture_loop_scope` leaves the event-loop semantics of 1025 async tests to whatever version resolves.
```python
[tool.pytest.ini_options]
asyncio_mode = "auto"
pythonpath = ["tests", "tests/golden", "tests/contract", "scripts"]
```
Evidence: `pyproject.toml:1`, `pyproject.toml:69`

### The fastapi skip guards are inconsistent and invisible to CI's skip detector [BAD] (severity: low)
Six files call `pytest.importorskip("fastapi")` with no reason, while `test_serve_compat.py` (17 tests), `test_jack_server.py` (18) and `test_errors.py` (16) import `fastapi.testclient` at module level. So a missing `[serve]` extra fails loudly today, and the six guards buy nothing. The residual risk is that their default skip reason matches none of CI's three patterns, and `fastapi` is absent from the importability probe. Remove the hard imports in a cleanup and roughly 35 tests, including the whole golden wire suite, drop out silently.
```python
pytest.importorskip("langfuse", reason="needs the [observability] extra")  # matches CI's grep
pytest.importorskip("fastapi")                                            # matches nothing
```
Evidence: `tests/test_langfuse_tracer.py:16`, `tests/golden/conftest.py:14`, `tests/test_serve.py:15`, `.github/workflows/ci.yml:83`

### check_contiguous is blind to duplicate seq, and several callers rely on it alone [BAD] (severity: low)
The suite's headline log-integrity helper compares a `set` of seq values against a range, so two events at the same seq read as clean. `test_multiprocess_concurrency.py` pairs it with a strict range equality that does catch duplicates; roughly a dozen other call sites do not, including a 20-trial cancel race.
```python
    seqs = {event.seq for event in run}
    return [n for n in range(max(seqs) + 1) if n not in seqs]
```
Making the helper return duplicates as well as gaps is a five-line change that upgrades every one of its 25 call sites.
Evidence: `tests/event_log_checks.py:29`, `tests/test_uc3_slowpoke.py:175`, `tests/test_multiprocess_concurrency.py:176`

### resolve_agent_mcp_servers is exported, uncalled, and untested [BAD] (severity: low)
A public re-export in `agentdeck.adapters.tools.mcp.__all__` with zero callers in `agentdeck/` and zero references in `tests/`. Its sibling `resolve_agent_mcp_status` is covered behaviorally through `source.py` and `compile.py`. Dead public surface is worse than dead private surface: it cannot be deleted without a deprecation, and it is untested because nothing needs it.
```python
def resolve_agent_mcp_servers(names: Iterable[str]) -> list[MCPServer]:
    return resolve_agent_mcp_status(names)[0]
```
Evidence: `agentdeck/adapters/tools/mcp/wiring.py:37`, `agentdeck/adapters/tools/mcp/__init__.py:16`

### Two of the scout's five coverage gaps are false positives [BAD] (severity: low)
Recorded so the next reader does not chase them. `telemetry/langfuse/client.py` is not a gap: `build_client` is exercised directly and, in `test_langfuse_tracer.py`, against the real installed SDK. `skills/bundle.py` is not a gap in behavior terms: `test_skills.py` covers frontmatter validation, name mismatch, missing description, lenient mode, duplicate names across roots and disclosure text, all through the `Skills` facade. `SkillBundle` never appearing by name is a test-style choice, not absent coverage.
```python
def test_build_client_yields_a_sink_over_the_real_sdk(spy) -> None:
    """The one construction point, against the SDK the package will actually run on."""
    client = build_client(
        LangfuseSettings(public_key="pk-lf-configured", secret_key="sk-lf-test", base_url="http://localhost:1")
    )
```
Evidence: `tests/test_langfuse_tracer.py:216`, `tests/test_observability.py:636`, `tests/test_skills.py:56`

## Bottom line

This suite is well above the median for an SDK at this stage: the contract matrix, the multiprocess races, the golden discipline, and the total absence of mocks are all genuinely hard to get right and are right here. The two things a real regression would walk through today are the MCP transport's self-healing path, which no test executes, and the schema forward-compat check, which is skipped on a reason that expired at v3.0.0. Fix those two, bound the two 0.5s waits, and split `test_deck.py` before it reaches 3000 lines.
