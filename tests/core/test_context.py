"""The carrier's application slot, and the public view over it.

``RunContext.data`` is the one field AgentDeck stores without ever interpreting, so the tests
that matter are about what it is *not*: not copied, not converted, not in the repr. ``Context``
is the view a user callable will be handed, and its surface is deliberately smaller than the
carrier's — a property that is only real if something asserts the missing names stay missing.
"""

from __future__ import annotations

import dataclasses

import pytest

from agentdeck.adapters.control.memory import MemoryControlPort
from agentdeck.core.context import REF_PREFIX, Context, RunContext, encode
from agentdeck.core.control import Gate, RunCancelledError, Signal


class Environment:
    """Stand-in for an application's own context object — a live handle, not data."""

    def __init__(self, token: str) -> None:
        self.token = token


def test_a_run_context_holds_no_application_data_unless_it_was_given_some() -> None:
    assert RunContext(run_id="r-1").data is None


def test_the_carrier_hands_back_the_same_object_it_was_given() -> None:
    """By reference, never a copy: the whole point is that a DB client survives the trip."""
    environment = Environment("t-1")
    assert RunContext(run_id="r-1", data=environment).data is environment


def test_application_data_is_absent_from_the_repr() -> None:
    """A context reaches log lines and tracebacks; the customer record it carries must not."""
    ctx = RunContext(run_id="r-1", namespace="acme", data=Environment("hunter2"))
    assert "hunter2" not in repr(ctx)
    assert "data" not in repr(ctx)
    assert "acme" in repr(ctx)


def test_application_data_cannot_be_swapped_mid_run() -> None:
    ctx = RunContext(run_id="r-1", data=Environment("t-1"))
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.data = Environment("t-2")  # ty: ignore[invalid-assignment] — that is the assertion


def test_the_public_view_exposes_the_application_object_and_the_run_identity() -> None:
    environment = Environment("t-1")
    ctx: Context[Environment] = Context(RunContext(run_id="r-1", session_id="s-1", data=environment))

    assert ctx.data is environment
    assert ctx.run_id == "r-1"
    assert ctx.session_id == "s-1"


def test_the_public_view_shares_the_run_s_reporter_rather_than_a_second_channel() -> None:
    run = RunContext(run_id="r-1")
    assert Context(run).reporter is run.reporter


def test_the_public_view_withholds_the_namespace_and_the_gate() -> None:
    """Both are deliberately out of the initial surface — an absence a later slice can add to,
    where a wrong meaning released once could not be taken back."""
    ctx = Context(RunContext(run_id="r-1", namespace="acme"))

    assert not hasattr(ctx, "namespace")
    assert not hasattr(ctx, "gate")


async def test_checkpoint_reaches_the_run_s_gate() -> None:
    control = MemoryControlPort()
    await control.signal("r-1", Signal.CANCEL)
    ctx = Context(RunContext(run_id="r-1", gate=Gate(control, "r-1")))

    with pytest.raises(RunCancelledError):
        await ctx.checkpoint()


async def test_checkpoint_on_an_unwired_run_is_a_no_op() -> None:
    """A callable holding a context built by hand still runs — the seam defaults to doing
    nothing rather than to needing a Runtime."""
    await Context(RunContext(run_id="r-1")).checkpoint()


# --- ref: the derived, namespace-aware address (#315) -----------------------------------


def test_encode_with_no_namespace_is_byte_identical_to_the_run_id() -> None:
    """The compatibility keystone: every unnamespaced ref is exactly today's run_id, so
    stored ids, the unnamespaced CLI and the frozen v1 wire need no migration at all."""
    assert encode(None, "order-1234") == "order-1234"
    assert RunContext(run_id="order-1234").ref == "order-1234"


def test_encode_namespaces_a_ref_so_it_cannot_collide_with_the_bare_run_id() -> None:
    acme_ref = encode("acme", "order-1234")
    globex_ref = encode("globex", "order-1234")

    assert acme_ref != "order-1234"
    assert globex_ref != "order-1234"
    assert acme_ref != globex_ref  # same caller run_id, two distinct addresses


def test_ref_is_derived_from_namespace_and_run_id_not_stored() -> None:
    ctx = RunContext(namespace="acme", run_id="order-1234")
    assert ctx.ref == encode("acme", "order-1234")
    assert not hasattr(ctx, "_ref")  # nothing to keep in sync with the pair it was computed from


def test_a_caller_supplied_run_id_starting_with_the_ref_prefix_is_refused() -> None:
    """Reserved because it is what makes an unnamespaced ref unambiguous: without this, a
    crafted ``run_id`` could be built to collide with a real namespaced ref (see ``encode``)."""
    with pytest.raises(ValueError, match=REF_PREFIX):
        RunContext(run_id=f"{REF_PREFIX}acme:order-1234")


def test_a_namespaced_run_id_starting_with_the_ref_prefix_is_also_refused() -> None:
    """Unconditional, not only when unnamespaced: the reservation is over the whole run_id
    space, so a caller cannot even hand it to a namespace to sidestep the check."""
    with pytest.raises(ValueError, match=REF_PREFIX):
        RunContext(namespace="acme", run_id=f"{REF_PREFIX}anything")
