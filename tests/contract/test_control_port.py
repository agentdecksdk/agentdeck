"""``ControlPort.consume``, held to one contract across both adapters.

The compare-and-set a honored intent needs. It exists because the alternative  -  clearing the
port, or writing ``RESUME`` over whatever is there  -  destroys a signal the writer never read,
and the one signal that reaches nothing else is a cancel recorded against a run that has already
stopped. So "take the intent I ruled on, and only that one" has to be a single operation the
store decides, not a poll followed by a write.

Both adapters are exercised on the same cases: the in-memory one is atomic for free, and the
SQLite one has to say so in a statement.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from agentdeck.adapters.control.memory import MemoryControlPort
from agentdeck.adapters.control.sqlite import SqliteControlPort
from agentdeck.core.control import ControlSignal, Signal

if TYPE_CHECKING:
    from collections.abc import Iterator

    from agentdeck.core.ports.control import ControlPort


@pytest.fixture(params=["memory", "sqlite"])
def control(request: pytest.FixtureRequest) -> Iterator[ControlPort]:
    if request.param == "memory":
        yield MemoryControlPort()
        return
    port = SqliteControlPort()
    try:
        yield port
    finally:
        port.close()


async def test_consuming_the_signal_that_is_pending_takes_it(control: ControlPort) -> None:
    """The ordinary case: a caller read ``cancel``, ruled on it, and takes it so nothing honors
    the same request twice. The port is empty afterwards, which is what a later poll has to see
     -  a sentinel left behind would read as an instruction to somebody."""
    await control.signal("r-1", Signal.CANCEL, "operator said stop")

    assert await control.consume("r-1", Signal.CANCEL) is True
    assert await control.poll("r-1") is None


async def test_consuming_a_signal_that_is_no_longer_pending_takes_nothing(control: ControlPort) -> None:
    """The case the whole method exists for. A caller read ``pause``, and by the time it went to
    take it an operator had recorded a ``cancel``  -  the one signal nothing else will ever notice.
    An unconditional clear would destroy it silently; the compare-and-set refuses and leaves it,
    so the losing caller re-polls and finds the cancel still there to act on.
    """
    await control.signal("r-1", Signal.PAUSE)
    await control.signal("r-1", Signal.CANCEL, "operator said stop")

    assert await control.consume("r-1", Signal.PAUSE) is False
    assert await control.poll("r-1") == ControlSignal(verb=Signal.CANCEL, reason="operator said stop")


async def test_consuming_an_empty_port_takes_nothing(control: ControlPort) -> None:
    """A run nobody has signaled, and a run whose signal a peer already took, answer the same
    way: ``False``, and no row invented for the asking."""
    assert await control.consume("never-signalled", Signal.CANCEL) is False
    assert await control.poll("never-signalled") is None


async def test_consuming_one_run_s_signal_leaves_every_other_run_s_alone(control: ControlPort) -> None:
    """Signals are addressed by ``run_id`` alone, so the taking has to be too  -  a port that
    cleared more than the run it was asked about would cancel other people's turns."""
    await control.signal("r-1", Signal.CANCEL)
    await control.signal("r-2", Signal.CANCEL)

    assert await control.consume("r-1", Signal.CANCEL) is True
    assert await control.poll("r-2") == ControlSignal(verb=Signal.CANCEL, reason=None)


async def test_only_one_of_two_callers_racing_for_the_same_signal_wins(control: ControlPort) -> None:
    """Two workers claiming the same parked run both read the same pending cancel. Exactly one
    may act on it: the second is told it holds a stale intent and re-polls rather than recording
    a second ``run.cancelled`` against a run that already has one.
    """
    await control.signal("r-1", Signal.CANCEL)

    outcomes = [await control.consume("r-1", Signal.CANCEL) for _ in range(2)]

    assert outcomes == [True, False]
    assert await control.poll("r-1") is None
