"""Declarative predicates over the event stream, composed with `|` / `&` / `~`.

An observer's `view=` picks a subset of the run's events; a view picks it by `kind`,
never by parsing what a payload holds  -  matching :mod:`agentdeck.observers`' own built-ins,
which are deliberately simple. ``lifecycle`` reuses :data:`agentdeck.core.status.LIFECYCLE_KINDS`,
the same kinds that move a run's status, rather than a second hand-kept list.

Reachable only through :data:`all`, by design: ``control.requested``, ``control.observed``
(signals, not transitions  -  ``LIFECYCLE_KINDS`` already excludes them for the same reason),
``node.updated``, ``artifact.created``, ``input.appended``, ``custom``, and any kind a newer
writer invented. None of the other six built-ins fits, and forcing one to would be a view that
means less than its name says.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentdeck.core.status import LIFECYCLE_KINDS

if TYPE_CHECKING:
    from collections.abc import Callable

    from agentdeck.core.events import Event


class View:
    """A reusable predicate over the event stream.

    ``|``/``&``/``~`` build a new :class:`View` rather than mutating either side, so a built-in
    stays reusable after it has been combined once.
    """

    def __init__(self, matches: Callable[[Event], bool]) -> None:
        self._matches = matches

    def matches(self, event: Event) -> bool:
        return self._matches(event)

    def __or__(self, other: View) -> View:
        return View(lambda event: self.matches(event) or other.matches(event))

    def __and__(self, other: View) -> View:
        return View(lambda event: self.matches(event) and other.matches(event))

    def __invert__(self) -> View:
        return View(lambda event: not self.matches(event))


def _kind_in(kinds: frozenset[str]) -> View:
    return View(lambda event: event.kind in kinds)


all = View(lambda _event: True)  # noqa: A001  -  `views.all`, the name the design doc rules
# ``agent.changed`` is here because a handoff replaces who is answering: a chat surface that
# omitted it would render replies that silently change author.
chat = _kind_in(frozenset({"text.delta", "thought.delta", "message.completed", "agent.changed"}))
tools = _kind_in(frozenset({"tool.call.started", "tool.call.completed"}))
reports = _kind_in(frozenset({"report"}))
lifecycle = _kind_in(LIFECYCLE_KINDS)
errors = _kind_in(frozenset({"run.failed", "answer.refused"}))
usage = _kind_in(frozenset({"usage.reported"}))

__all__ = ["View", "all", "chat", "errors", "lifecycle", "reports", "tools", "usage"]
