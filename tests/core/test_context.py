"""The carrier's application slot, and the public view over it.

``RunContext.data`` is the one field AgentDeck stores without ever interpreting, so the tests
that matter are about what it is *not*: not copied, not converted, not in the repr. ``Context``
is the view a user callable will be handed, and its surface is deliberately smaller than the
carrier's  -  a property that is only real if something asserts the missing names stay missing.
"""

from __future__ import annotations

import dataclasses

import pytest

from agentdeck.adapters.control.memory import MemoryControlPort
from agentdeck.core.context import Context, RunContext
from agentdeck.core.control import Gate, RunCancelledError, Signal


class Environment:
    """Stand-in for an application's own context object  -  a live handle, not data."""

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
        ctx.data = Environment("t-2")  # ty: ignore[invalid-assignment]  -  that is the assertion


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
    """Both are deliberately out of the initial surface  -  an absence a later slice can add to,
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
    """A callable holding a context built by hand still runs  -  the seam defaults to doing
    nothing rather than to needing a Runtime."""
    await Context(RunContext(run_id="r-1")).checkpoint()


# --- id: a carried value, not a computed one (#324) -------------------------------------


def test_id_is_a_plain_read_of_run_id_not_a_derivation() -> None:
    """The compatibility keystone survives minting for a different reason now: there is only
    one field, so there is nothing left to derive and nothing it could disagree with."""
    ctx = RunContext(run_id="order-1234")
    assert ctx.id == "order-1234"


def test_id_carries_the_namespaced_run_ids_own_value_unchanged() -> None:
    """Unlike the derivation it replaces, id does not fold namespace into the value at all  -
    two namespaces sharing one run_id would collide here, which is exactly why run_id is
    minted rather than caller-supplied once a real run starts (see ``Runtime._new_run_context``)."""
    ctx = RunContext(namespace="acme", run_id="order-1234")
    assert ctx.id == "order-1234"
    assert not hasattr(ctx, "_id")  # no second field a plain read of run_id could disagree with


def test_key_defaults_to_none_and_plays_no_part_in_id_or_log_key() -> None:
    ctx = RunContext(run_id="r-1", session_id="s-1")
    assert ctx.key is None

    keyed = RunContext(run_id="r-1", session_id="s-1", key="order-1234")
    assert keyed.id == ctx.id
    assert keyed.log_key == ctx.log_key
