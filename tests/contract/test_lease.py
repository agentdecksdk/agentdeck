"""Lease contract: what every ``LeasePort`` must promise, and the one promise the whole
design rests on  -  a run the port has never seen is never reported dead.

Parametrized over both adapters. Redis and Postgres are absent because ``AGENTDECK_CONTROL``
has no scheme for them yet (``ControlSettings`` records that as deferred), so a lease port for
either would be unreachable code; they arrive when the control port does.

The expiry cases move a clock rather than sleep through one: memory takes an injected clock,
and SQLite reads its own, so there a negative TTL is what puts an expiry in the past. Both
arrive at the same place  -  a lease this port wrote and then watched pass.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from agentdeck.adapters.leases.memory import MemoryLeasePort
from agentdeck.adapters.leases.sqlite import SqliteLeasePort

if TYPE_CHECKING:
    from collections.abc import Iterator

    from agentdeck.core.ports import LeasePort

TTL = timedelta(seconds=60)
EXPIRED = timedelta(seconds=-1)


class _Held:
    """A clock a test moves by hand, so an expiry can be in the past without a wall-clock wait."""

    def __init__(self) -> None:
        self.at = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.at


@pytest.fixture(params=["memory", "sqlite"])
def lease(request: pytest.FixtureRequest) -> Iterator[LeasePort]:
    if request.param == "memory":
        yield MemoryLeasePort(clock=_Held())
        return
    port = SqliteLeasePort()
    yield port
    port.close()


async def test_a_run_the_port_has_never_seen_is_not_dead(lease: LeasePort) -> None:
    """**The load-bearing case.** ``dead()`` reports positive knowledge and nothing else.

    The tempting one-line implementation is ``run_id not in table``, which reads "no lease
    recorded" as "the worker died". It is wrong in the one deployment that matters: a second
    process's port has no record of the first process's live runs, so every worker would take
    over every peer's work on sight. Absence of knowledge must come back empty.
    """
    assert await lease.dead(["never-seen", "nor-this-one"]) == frozenset()


async def test_a_run_whose_lease_expired_is_dead(lease: LeasePort) -> None:
    """The knowledge the port does have: it wrote this expiry itself and then watched it pass."""
    assert await lease.acquire("r-1", EXPIRED) is True

    assert await lease.dead(["r-1"]) == frozenset({"r-1"})


async def test_a_run_holding_a_live_lease_is_not_dead(lease: LeasePort) -> None:
    """A worker still renewing is not a worker that died, which is the whole assertion."""
    await lease.acquire("r-1", TTL)

    assert await lease.dead(["r-1"]) == frozenset()


async def test_a_released_lease_leaves_no_ghost(lease: LeasePort) -> None:
    """Releasing is forgetting, not recording a death. A run whose lease was released cleanly
    is back to being one the port knows nothing about  -  otherwise every completed run would
    accumulate as a permanent "dead" answer for whatever asks next."""
    await lease.acquire("r-1", EXPIRED)
    await lease.release("r-1")

    assert await lease.dead(["r-1"]) == frozenset()


async def test_releasing_a_lease_nobody_holds_is_not_an_error(lease: LeasePort) -> None:
    """Every exit path releases, and only some of them acquired anything."""
    await lease.release("never-held")


async def test_a_live_lease_refuses_a_second_holder(lease: LeasePort) -> None:
    """Two workers cannot both assert they are executing one run."""
    assert await lease.acquire("r-1", TTL) is True

    assert await lease.acquire("r-1", TTL) is False


async def test_an_expired_lease_can_be_taken_by_the_next_worker(lease: LeasePort) -> None:
    """The recovery path: the killed worker's row does not block the turn that takes over."""
    await lease.acquire("r-1", EXPIRED)

    assert await lease.acquire("r-1", TTL) is True


async def test_renewing_pushes_the_expiry_out(lease: LeasePort) -> None:
    """What a live run does six times a TTL, and it has to actually clear the expiry."""
    await lease.acquire("r-1", EXPIRED)
    assert await lease.dead(["r-1"]) == frozenset({"r-1"})

    assert await lease.renew("r-1", TTL) is True
    assert await lease.dead(["r-1"]) == frozenset()


async def test_renewing_a_lease_that_is_gone_says_so(lease: LeasePort) -> None:
    """How a run finds out it lost its lease while it was still playing  -  the caller logs it
    rather than guessing from silence."""
    assert await lease.renew("never-held", TTL) is False


async def test_asking_about_nothing_answers_nothing(lease: LeasePort) -> None:
    """A session whose log is empty asks about no runs at all, which must not become a query
    for every lease in the backend."""
    assert await lease.dead([]) == frozenset()


async def test_two_ports_on_one_sqlite_file_see_each_others_leases(tmp_path) -> None:
    """The headline case, in the small: the killed worker and the one taking over share only a
    file. This is what a memory lease cannot do, and the reason the sqlite adapter exists."""
    db = str(tmp_path / "control.db")
    killed, survivor = SqliteLeasePort(db), SqliteLeasePort(db)
    try:
        await killed.acquire("r-1", TTL)
        assert await survivor.dead(["r-1"]) == frozenset(), "a live peer's run is not dead"

        await killed.renew("r-1", EXPIRED)  # the worker stops renewing and the lease lapses

        assert await survivor.dead(["r-1"]) == frozenset({"r-1"})
    finally:
        killed.close()
        survivor.close()


async def test_two_memory_ports_know_nothing_of_each_other() -> None:
    """And the counterpart, which is why the default is safe: two processes each holding an
    in-memory lease report nothing about the other's runs, so nothing is ever taken over and
    the staleness timer stays the only backstop."""
    killed, survivor = MemoryLeasePort(), MemoryLeasePort()
    await killed.acquire("r-1", EXPIRED)

    assert await killed.dead(["r-1"]) == frozenset({"r-1"}), "its own expiry, it knows"
    assert await survivor.dead(["r-1"]) == frozenset(), "the peer's, it does not"
