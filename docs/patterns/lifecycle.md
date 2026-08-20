# Run Lifecycle

One concept has one home. Externally initiated lifecycle operations (cancel, pause, resume, answer) are methods on the existing handles (`Run` in `deck.py`, `Runtime.signal` in `runtime/service.py`); state transitions flow through the runtime's state machine.

Good (real, `deck.py`): timeout-like behavior asks the existing handle:

```python
await run.cancel(reason="timeout")
```

Bad, the classic agent artifact:

```python
class TimeoutManager:            # second lifecycle path
    def _terminate_run(self): ...
```

Rules:
- No `*Manager`/`*Controller`/`*Handler` class whose responsibility an existing handle owns; check `uv run scripts/repomap.py` first.
- Never mutate run state directly; go through the transition path.
- `asyncio.create_task` belongs to the runtime (`runtime/dispatch.py`, `runtime/service.py`, `deck.py`); a task created elsewhere bypasses the run's cancellation and drain guarantees (enforced by ruff TID251).
- Control verbs are signals, not errors: honoring a cancel raises `ControlSignalled`, which records the effect.
