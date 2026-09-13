"""The report channel: what an emitter gets whether or not anything is listening.

The default reporter is the one most code will meet  -  a ``RunContext`` a caller built, a
context reused outside a run  -  and it has to be indistinguishable from a wired one except that
nobody reads the result. Including for a *bad* call: an emitter that only finds out its report is
malformed when it happens to run under a Runtime has no way to test itself.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentdeck.core import Reported, Reporter, RunContext


def _writing() -> tuple[Reporter, list[Reported]]:
    """A reporter wired the way the Runtime wires one, and what its writer was handed."""
    written: list[Reported] = []
    return Reporter(written.append), written


def test_a_report_becomes_a_payload_in_the_order_it_was_made() -> None:
    reporter, written = _writing()
    reporter.info("Searching GitHub")
    reporter.warning("Primary source unavailable", source="drive")
    assert written == [
        Reported(level="info", message="Searching GitHub"),
        Reported(level="warning", message="Primary source unavailable", fields={"source": "drive"}),
    ]


def test_the_four_methods_differ_only_in_level() -> None:
    """One payload, four things it can be: three severities a person reads and a record a
    consumer filters. A record's name is its message, so a reader with no schema still has
    something to show."""
    reporter, written = _writing()
    reporter.info("looking")
    reporter.warning("degraded")
    reporter.error("index lookup failed", index="customers")
    reporter.report("candidate_found", score=0.91)

    assert [(payload.level, payload.message) for payload in written] == [
        ("info", "looking"),
        ("warning", "degraded"),
        ("error", "index lookup failed"),
        ("record", "candidate_found"),
    ]


def test_the_default_reporter_drops_instead_of_raising() -> None:
    """A context with no writer must not fail the code that reports into it."""
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


def test_a_flood_reaches_the_writer_whole() -> None:
    """No cap on the way out any more (#487): what a run may write is the log's business, and a
    store that refuses an append is the ceiling. Holding a backlog to drop from is what made the
    64th report the last one a long call could make."""
    reporter, written = _writing()
    for n in range(500):
        reporter.report("step", n=n)

    assert [payload.fields["n"] for payload in written] == list(range(500))
