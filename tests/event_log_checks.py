"""Event-log invariant checks the test suite asserts on, not the schema.

``check_contiguous``/``check_terminal`` used to live in ``agentdeck.core.events``, but neither
one is read by a production path: ``seq`` contiguity is a property of how the store assigns it,
and "exactly one terminal event, last" is enforced by ``Runtime.run``/``resume`` stopping the read
loop at a terminal payload. Keeping the checks in the schema module read as if they were part of
the contract a producer or consumer has to satisfy, when they are really the test suite's own way
of measuring whether the real mechanisms held  -  so they moved here, next to the tests that use
them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentdeck.core.events import TERMINAL_KINDS

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from agentdeck.core.events import Event


def check_contiguous(events: Iterable[Event]) -> list[int]:
    """Missing ``seq`` numbers for one run  -  gaps only, duplicates aren't checked."""
    run = list(events)
    if len({event.run_id for event in run}) > 1:
        raise ValueError("check_contiguous takes one run's events")
    seqs = {event.seq for event in run}
    if not seqs:
        return []
    return [n for n in range(max(seqs) + 1) if n not in seqs]


def check_terminal(events: Sequence[Event]) -> str | None:
    """``None`` if exactly one terminal event closes the run, else what's wrong."""
    at = [i for i, event in enumerate(events) if event.kind in TERMINAL_KINDS]
    if not at:
        return "no terminal event"
    if len(at) > 1:
        return f"{len(at)} terminal events: {[events[i].kind for i in at]}"
    if at[0] != len(events) - 1:
        return f"terminal event {events[at[0]].kind!r} at index {at[0]} of {len(events)}, not last"
    return None
