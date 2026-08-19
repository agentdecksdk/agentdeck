"""A conversation memory that forgets its oldest exchanges instead of growing forever.

Every turn appends the question, every tool call, and every tool result to the session, and the
whole session is re-sent on the next turn. Tool results here are documentation pages: one of them
is 30 KB on its own. Fifteen turns of that reached a 320 KB request, and once a session is too
large for the model endpoint it stays too large  -  the conversation is dead and reloading is the
only way out, which is not something a reader will guess.

So the history is bounded. What makes this safe is *where* it cuts: a tool call and its result
must never be separated, because a call with no result is malformed input and fails the request
for a different reason than the one being fixed. Cutting only at the start of an exchange, at the
user message that begins it, keeps every call with its result.

The store still holds everything; this bounds what is *sent*. The full record stays in the event
log, which is what an audit reads.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agents.memory import SQLiteSession

if TYPE_CHECKING:
    from agents.memory import Session

# Characters, not tokens: the budget exists to keep requests well clear of the endpoint's limit,
# and an exact token count would need the model's tokeniser to buy nothing here. Roughly 25k
# tokens, which is a long conversation and a small fraction of any current context window.
DEFAULT_BUDGET = 100_000


def _size(item: Any) -> int:
    """Rough character cost of one history item. Cheap and monotonic is all this needs to be."""
    return len(str(item))


def _starts_an_exchange(item: Any) -> bool:
    return isinstance(item, dict) and item.get("role") == "user"


def within_budget(items: list[Any], budget: int) -> list[Any]:
    """The most recent whole exchanges that fit, oldest dropped first.

    Never returns a fragment of an exchange, so a tool call is never separated from its result.
    A single exchange larger than the budget is returned whole: dropping it would lose the turn
    the reader just took, and truncating it would produce exactly the malformed input this
    function exists to avoid.
    """
    starts = [i for i, item in enumerate(items) if _starts_an_exchange(item)]
    if not starts:
        return items

    running = 0
    keep_from = starts[-1]
    for cut in reversed(starts):
        running = sum(_size(item) for item in items[cut:])
        if running > budget and cut != starts[-1]:
            break
        keep_from = cut
    return items[keep_from:]


class BoundedSession:
    """Wraps a session and hands the model only its most recent exchanges."""

    def __init__(self, inner: Session, budget: int = DEFAULT_BUDGET) -> None:
        self._inner = inner
        self._budget = budget
        # Plain attributes, not properties: the `Session` protocol declares both writable.
        self.session_id = inner.session_id
        self.session_settings = getattr(inner, "session_settings", None)

    async def get_items(self, limit: int | None = None) -> list[Any]:
        return within_budget(await self._inner.get_items(limit), self._budget)

    async def add_items(self, items: list[Any]) -> None:
        await self._inner.add_items(items)

    async def pop_item(self) -> Any:
        return await self._inner.pop_item()

    async def clear_session(self) -> None:
        await self._inner.clear_session()


class BoundedSessions:
    """A `Deck(session_factory=...)`: one bounded in-process session per key.

    The default factory builds a plain `SQLiteSession` with no bound, which is the right default
    for a private application and the wrong one for a public endpoint whose turn count is capped
    but whose page sizes are not.
    """

    def __init__(self, budget: int = DEFAULT_BUDGET) -> None:
        self._budget = budget
        self._sessions: dict[str, BoundedSession] = {}

    def session_for(self, session_id: str) -> Session:
        if session_id not in self._sessions:
            self._sessions[session_id] = BoundedSession(SQLiteSession(session_id), self._budget)
        return self._sessions[session_id]

    async def aclose(self) -> None:
        self._sessions.clear()
