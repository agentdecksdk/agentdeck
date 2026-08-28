"""The report channel: what an emitter gets whether or not anything is listening.

The default reporter is the one most code will meet  -  a ``RunContext`` a caller built, a
context reused outside a run  -  and it has to be indistinguishable from a wired one except that
nobody reads the result. Including for a *bad* call: an emitter that only finds out its report is
malformed when it happens to run under a Runtime has no way to test itself.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from agentdeck.core import Reported, Reporter, RunContext
from agentdeck.core.reporting import MAX_PENDING_REPORTS, SyncReporter

if TYPE_CHECKING:
    from agentdeck.core.base import JsonData
    from agentdeck.core.events import KnownPayload


def _pending() -> tuple[Reporter, deque[KnownPayload]]:
    """A reporter wired the way the Runtime wires one, and the buffer it writes into."""
    buffer: deque[KnownPayload] = deque()
    return Reporter(buffer), buffer


async def test_a_report_becomes_a_payload_in_the_order_it_was_made() -> None:
    reporter, buffer = _pending()
    await reporter.info("Searching GitHub")
    await reporter.warning("Primary source unavailable", source="drive")
    assert list(buffer) == [
        Reported(level="info", message="Searching GitHub"),
        Reported(level="warning", message="Primary source unavailable", fields={"source": "drive"}),
    ]


async def test_the_four_methods_differ_only_in_level() -> None:
    """One payload, four things it can be: three severities a person reads and a record a
    consumer filters. A record's name is its message, so a reader with no schema still has
    something to show."""
    reporter, buffer = _pending()
    await reporter.info("looking")
    await reporter.warning("degraded")
    await reporter.error("index lookup failed", index="customers")
    await reporter.report("candidate_found", score=0.91)

    assert [(payload.level, payload.message) for payload in buffer] == [
        ("info", "looking"),
        ("warning", "degraded"),
        ("error", "index lookup failed"),
        ("record", "candidate_found"),
    ]


async def test_the_default_reporter_drops_instead_of_raising() -> None:
    """A context nothing is draining must not fail the code that reports into it."""
    ctx = RunContext(namespace="acme", run_id="r-1")
    await ctx.reporter.info("nobody is listening")
    await ctx.reporter.report("still_nobody", n=1)


async def test_the_default_reporter_still_validates() -> None:
    """Dropped is not unvalidated: the same call fails the same way wired or not, so a tool's
    own tests catch an empty report without a Runtime."""
    reporter = Reporter()
    with pytest.raises(ValidationError):
        await reporter.info("")
    with pytest.raises(ValidationError):
        await reporter.report("")


async def test_a_flood_is_bounded_dropping_the_newest_and_saying_so(caplog) -> None:
    """The buffer is filled by an invocable's own code, so it is bounded. The front survives:
    a sequence read with its beginning missing looks like a run that started at 40.
    """
    reporter, buffer = _pending()
    with caplog.at_level(logging.WARNING, logger="agentdeck.core.reporting"):
        for n in range(MAX_PENDING_REPORTS + 5):
            await reporter.report("step", n=n)

    assert len(buffer) == MAX_PENDING_REPORTS
    assert [payload.fields["n"] for payload in buffer] == list(range(MAX_PENDING_REPORTS))
    assert "dropping report" in caplog.text


async def test_a_drained_buffer_takes_reports_again() -> None:
    """What the Runtime does between two engine payloads, in miniature: the cap is a backlog
    limit, not a per-run quota."""
    reporter, buffer = _pending()
    for n in range(MAX_PENDING_REPORTS):
        await reporter.report("step", n=n)
    buffer.clear()
    await reporter.info("still reporting")
    assert list(buffer) == [Reported(level="info", message="still reporting")]


def test_sync_reporter_blocks_the_worker_until_the_report_actually_lands() -> None:
    """The bridge's guarantee is that ``.result()`` blocks, not merely that a fast report
    happens to arrive first: two instant reports pass even with the block removed, because
    ``call_soon_threadsafe`` delivers FIFO regardless of whether the submitter waited. A report
    that takes real wall-clock time to land is what actually exercises the block  -  proven by
    asserting the buffer already holds it the instant ``.info()`` returns."""
    buffer: deque[KnownPayload] = deque()

    class _SlowReporter(Reporter):
        async def info(self, message: str, **fields: JsonData) -> None:
            await asyncio.sleep(0.05)
            await super().info(message, **fields)

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        SyncReporter(_SlowReporter(buffer), loop).info("slow report")
        assert list(buffer) == [Reported(level="info", message="slow report")]
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        loop.close()
