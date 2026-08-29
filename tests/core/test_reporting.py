"""The report channel: what an emitter gets whether or not anything is listening.

The default reporter is the one most code will meet  -  a ``RunContext`` a caller built, a
context reused outside a run  -  and it has to be indistinguishable from a wired one except that
nobody reads the result. Including for a *bad* call: an emitter that only finds out its report is
malformed when it happens to run under a Runtime has no way to test itself.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from agentdeck.core import Reported, Reporter, RunContext
from agentdeck.core.reporting import MAX_PENDING_REPORTS

if TYPE_CHECKING:
    from agentdeck.core.events import KnownPayload


def _pending() -> tuple[Reporter, deque[KnownPayload]]:
    """A reporter wired the way the Runtime wires one, and the buffer it writes into."""
    buffer: deque[KnownPayload] = deque()
    return Reporter(buffer), buffer


def test_a_report_becomes_a_payload_in_the_order_it_was_made() -> None:
    reporter, buffer = _pending()
    reporter.info("Searching GitHub")
    reporter.warning("Primary source unavailable", source="drive")
    assert list(buffer) == [
        Reported(level="info", message="Searching GitHub"),
        Reported(level="warning", message="Primary source unavailable", fields={"source": "drive"}),
    ]


def test_the_four_methods_differ_only_in_level() -> None:
    """One payload, four things it can be: three severities a person reads and a record a
    consumer filters. A record's name is its message, so a reader with no schema still has
    something to show."""
    reporter, buffer = _pending()
    reporter.info("looking")
    reporter.warning("degraded")
    reporter.error("index lookup failed", index="customers")
    reporter.report("candidate_found", score=0.91)

    assert [(payload.level, payload.message) for payload in buffer] == [
        ("info", "looking"),
        ("warning", "degraded"),
        ("error", "index lookup failed"),
        ("record", "candidate_found"),
    ]


def test_the_default_reporter_drops_instead_of_raising() -> None:
    """A context nothing is draining must not fail the code that reports into it."""
    ctx = RunContext(namespace="acme", run_id="r-1")
    ctx.reporter.info("nobody is listening")
    ctx.reporter.report("still_nobody", n=1)


def test_the_default_reporter_still_validates() -> None:
    """Dropped is not unvalidated: the same call fails the same way wired or not, so a tool's
    own tests catch an empty report without a Runtime."""
    reporter = Reporter()
    with pytest.raises(ValidationError):
        reporter.info("")
    with pytest.raises(ValidationError):
        reporter.report("")


def test_a_flood_is_bounded_dropping_the_newest_and_saying_so(caplog) -> None:
    """The buffer is filled by an invocable's own code, so it is bounded. The front survives:
    a sequence read with its beginning missing looks like a run that started at 40.
    """
    reporter, buffer = _pending()
    with caplog.at_level(logging.WARNING, logger="agentdeck.core.reporting"):
        for n in range(MAX_PENDING_REPORTS + 5):
            reporter.report("step", n=n)

    assert len(buffer) == MAX_PENDING_REPORTS
    assert [payload.fields["n"] for payload in buffer] == list(range(MAX_PENDING_REPORTS))
    assert "dropping report" in caplog.text


def test_a_drained_buffer_takes_reports_again() -> None:
    """What the Runtime does between two engine payloads, in miniature: the cap is a backlog
    limit, not a per-run quota."""
    reporter, buffer = _pending()
    for n in range(MAX_PENDING_REPORTS):
        reporter.report("step", n=n)
    buffer.clear()
    reporter.info("still reporting")
    assert list(buffer) == [Reported(level="info", message="still reporting")]


def test_the_lock_closes_the_check_then_act_race_at_the_cap() -> None:
    """The cap check and the append are two operations, not one  -  each is atomic by itself
    under the GIL, but two threads interleaving between them could both see room and both
    append, one past the cap. A deliberately slow first check opens exactly that window for a
    concurrent second call; the lock is what has to close it back up."""
    entered_check = threading.Event()

    class _SlowOnFirstCheck(deque):
        def __len__(self) -> int:
            length = super().__len__()
            if not entered_check.is_set():
                entered_check.set()
                time.sleep(0.05)
            return length

    buffer = _SlowOnFirstCheck(
        Reported(level="record", message="step", fields={"n": n}) for n in range(MAX_PENDING_REPORTS - 1)
    )
    reporter = Reporter(buffer)

    first = threading.Thread(target=lambda: reporter.report("step", n=100))
    first.start()
    assert entered_check.wait(timeout=1), "the first call never reached its length check"
    reporter.report("step", n=101)
    first.join(timeout=1)

    assert len(buffer) == MAX_PENDING_REPORTS
