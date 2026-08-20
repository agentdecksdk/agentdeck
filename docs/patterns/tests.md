# Tests

A test asserts an observable invariant, offline and deterministic. If it passes with the behavior broken, it is not a test.

Good (real, `tests/core/test_content.py`): asserts the contract, not the internals:

```python
def test_coercion_is_idempotent():
    once = coerce_input("hi")
    assert coerce_input(once) == once  # the double-wrap guard: no [[TextBlock]]
```

Rules:
- Model calls are scripted: `ScriptedModel` / `patch_model` / `scripted_model_server` from `agentdeck.testing`. A hand-rolled fourth fake is a defect.
- Stub only at the engine SDK boundary; asserting on your own mock is asserting nothing.
- No network, no live model, no time-dependent sleeps; `timeout=` on every subprocess.
- `skip`/`xfail` carry an issue ref (`reason="engine bug #311"`), enforced by SLOP008.
- Test names state the invariant (`test_a_non_finite_float_is_rejected_rather_than_serialized_as_null`), not the method under test.
