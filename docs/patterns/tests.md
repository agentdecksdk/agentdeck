# Tests

A test asserts an observable invariant, offline and deterministic. If it passes with the behavior broken, it is not a test.

Good (real, `tests/core/test_content.py`): asserts the contract, not the internals:

```python
def test_coercion_is_idempotent():
    once = coerce_input("hi")
    assert coerce_input(once) == once  # the double-wrap guard: no [[TextBlock]]
```

Good (real, `tests/test_run_reporting.py`, #693): the second report is made while the first is still suspended inside `append`, so an unordered write lands first and fails the assert:

```python
class _ReportGate(MemoryEventStore):
    async def append(self, payloads, ctx, origin):
        if self._hold and any(isinstance(p, Reported) for p in payloads):
            self._hold = False
            self.holding.set()
            await self.release.wait()
        return await super().append(payloads, ctx, origin)
```

The same shape gates a different seam in three other merged tests: `test_store.py`'s original (#421), its completion-side sibling (#471, #680), and `_ClaimThenStall` in `test_runtime_service.py` (#391, #682), which gates a read instead of a write.

Bad, illustrative (the repo has no real instance; racing sleeps are already banned): `await asyncio.sleep(0.05)` between the two writes, then an assert on the order. It passes on a machine that happens to schedule them that way, which is every machine until CI is loaded.

Rules:
- Model calls are scripted: `ScriptedModel` / `patch_model` / `scripted_model_server` from `agentdeck.testing`. A hand-rolled fourth fake is a defect.
- Stub only at the engine SDK boundary; asserting on your own mock is asserting nothing.
- No network, no live model, no time-dependent sleeps; `timeout=` on every subprocess.
- `skip`/`xfail` carry an issue ref (`reason="engine bug #311"`), enforced by SLOP008.
- Test names state the invariant (`test_a_non_finite_float_is_rejected_rather_than_serialized_as_null`), not the method under test.
- A test for a concurrency invariant holds the window open, never races for it: gate the operation inside the seam that makes it indivisible, then perform the second operation while the first is still suspended there. A sleep passes only when the machine happens to be fast enough; the held-open shape fails deterministically when the invariant breaks.
