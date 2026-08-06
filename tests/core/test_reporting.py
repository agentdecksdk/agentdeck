"""The report channel: what an emitter gets whether or not anything is listening.

The default reporter is the one most code will meet — a ``RunContext`` a caller built, a
context reused outside a run — and it has to be indistinguishable from a wired one except that
nobody reads the result. Including for a *bad* call: an emitter that only finds out its numbers
are nonsense when it happens to run under a Runtime has no way to test itself.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from agentdeck.core import ProgressReported, Reporter, RunContext, StatusReported
from agentdeck.core.reporting import MAX_PENDING_REPORTS

if TYPE_CHECKING:
    from agentdeck.core.events import KnownPayload


def _pending() -> tuple[Reporter, deque[KnownPayload]]:
    """A reporter wired the way the Runtime wires one, and the buffer it writes into."""
    buffer: deque[KnownPayload] = deque()
    return Reporter(buffer), buffer


async def test_a_report_becomes_a_payload_in_the_order_it_was_made() -> None:
    reporter, buffer = _pending()
    await reporter.status("Searching GitHub")
    await reporter.progress("Reviewing issues", current=2, total=4)
    assert list(buffer) == [
        StatusReported(message="Searching GitHub"),
        ProgressReported(step="Reviewing issues", current=2, total=4),
    ]


async def test_the_default_reporter_drops_instead_of_raising() -> None:
    """A context nothing is draining must not fail the code that reports into it."""
    ctx = RunContext(tenant="acme", principal="user:1", run_id="r-1", trace_id="tr-1")
    await ctx.reporter.status("nobody is listening")
    await ctx.reporter.progress("still nobody", current=1, total=2)


async def test_the_default_reporter_still_validates() -> None:
    """Dropped is not unvalidated: the same call fails the same way wired or not, so a tool's
    own tests catch a bad count without a Runtime."""
    reporter = Reporter()
    with pytest.raises(ValidationError, match="past total"):
        await reporter.progress("Reviewing issues", current=9, total=4)
    with pytest.raises(ValidationError):
        await reporter.status("")


async def test_a_flood_is_bounded_dropping_the_newest_and_saying_so(caplog) -> None:
    """The buffer is filled by an invocable's own code, so it is bounded. The front survives:
    a progress sequence read with its beginning missing looks like a run that started at 40.
    """
    reporter, buffer = _pending()
    with caplog.at_level(logging.WARNING, logger="agentdeck.core.reporting"):
        for n in range(MAX_PENDING_REPORTS + 5):
            await reporter.progress("step", current=n, total=MAX_PENDING_REPORTS + 10)

    assert len(buffer) == MAX_PENDING_REPORTS
    assert [payload.current for payload in buffer] == list(range(MAX_PENDING_REPORTS))
    assert "dropping progress.reported" in caplog.text


async def test_a_drained_buffer_takes_reports_again() -> None:
    """What the Runtime does between two engine payloads, in miniature: the cap is a backlog
    limit, not a per-run quota."""
    reporter, buffer = _pending()
    for n in range(MAX_PENDING_REPORTS):
        await reporter.progress("step", current=n)
    buffer.clear()
    await reporter.status("still reporting")
    assert list(buffer) == [StatusReported(message="still reporting")]
