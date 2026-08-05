"""Bringing the SDK session back in line with the event log after a crash between the
two writes (ADR-D5: the log records the intent, the session is the engine's working memory).

A turn writes the log first and the session second, so a process that dies in between
leaves the log holding a message the session never got — a question the model would
otherwise never see, or an answer it would think it never gave. On the next turn this
compares the two message-level transcripts and appends whatever the session is missing.

Message level means content and order, nothing more, and that has a lasting cost: the log
stores tool results truncated and never stores reasoning items, so a repaired session holds
plain text where an intact one held paired tool-call/tool-result items and reasoning, and it
keeps holding it for the rest of that conversation. The model can then see an answer with no
evidence of the tool call behind it. Accepted deliberately — the alternative is a turn the
model cannot see at all.

Two things are never replayed. An abandoned run's input: ``run.started`` says a turn was
asked for, not that the engine took it, so replaying it would land in front of the question
the user is about to retry. And anything at all into a session that has gone somewhere the
log's prefix does not cover: that session is the authority on execution, a wrong guess about
its tail is worse than a gap, and the disagreement is reported instead.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Literal
from weakref import WeakKeyDictionary

from agentdeck.core.content import TextBlock
from agentdeck.core.events import Custom, InputAppended, MessageCompleted, RunCancelled, RunStarted

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agents.items import TResponseInputItem
    from agents.memory.session import Session

    from agentdeck.core.content import Input
    from agentdeck.core.events import Event

logger = logging.getLogger(__name__)

Role = Literal["user", "assistant"]
Message = tuple[Role, str]
"""One transcript entry: the role that spoke and the text it said."""

DIVERGED = "openai_agents.session_diverged"
"""This engine's own event for "the two stores disagree about more than a missing tail"."""

_LOCKS: WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Lock]] = WeakKeyDictionary()


async def reconcile(session: Session, history: Sequence[Event]) -> Custom | None:
    """Append the messages ``history`` records and ``session`` is missing, before a turn runs.

    Returns a ``custom`` payload when the two disagree about more than a missing tail, for
    the caller to yield: a log line alone cannot be noticed, and the run is still perfectly
    runnable on the session it has. ``None`` means nothing to do, or a repair that went in.
    """
    # ponytail: whole log against whole session, every turn — the same ceiling the Runtime's
    # own per-turn log read already has, and the SDK reads the whole session anyway. Both want
    # windowing together, once a session's history outgrows one read.
    logged = _log_transcript(history)
    if not logged:
        return None
    # Read-then-append is atomic only under this lock, so two turns racing on one session in
    # one process cannot both apply the same repair and double the conversation. One process is
    # as far as it reaches: two servers on one session are stopped at the door by the session
    # claim of #83, never by anything here.
    async with _lock_for(session.session_id):
        stored = _session_transcript(await session.get_items())
        shared = min(len(stored), len(logged))
        if stored[:shared] != logged[:shared]:
            at = next(index for index in range(shared) if stored[index] != logged[index])
            logger.warning(
                "session %s disagrees with its log from message %d (%d session messages, %d logged): replaying nothing",
                session.session_id,
                at,
                len(stored),
                len(logged),
            )
            return Custom(
                name=DIVERGED,
                data={"agreed_through": at, "session_messages": len(stored), "logged_messages": len(logged)},
            )
        missing = logged[len(stored) :]
        if not missing:
            # Equal, or the session is ahead — which an input the log deliberately leaves out
            # (an abandoned turn the engine did take) is enough to cause, with nothing to add.
            return None
        logger.info("replaying %d logged message(s) the session is missing into %s", len(missing), session.session_id)
        await session.add_items([_as_item(role, text) for role, text in missing])
    return None


def _lock_for(session_id: str) -> asyncio.Lock:
    """One lock per session, per event loop: a lock outlives neither, so one left behind by a
    finished loop can never be acquired again."""
    per_loop = _LOCKS.setdefault(asyncio.get_running_loop(), {})
    lock = per_loop.get(session_id)
    if lock is None:
        lock = per_loop[session_id] = asyncio.Lock()
    return lock


def _log_transcript(history: Sequence[Event]) -> list[Message]:
    """Every message the log says entered or left the loop, in order.

    A turn's input lands on ``run.started``, mid-turn steering on ``input.appended``, the
    assistant's side on ``message.completed``. Deltas are streaming UX and tool traffic is not
    message level, so neither belongs here.

    An abandoned run contributes no input: a consumer that walked away (``run.cancelled``) may
    well have done so before the engine read anything, and the user's retry would then arrive
    behind a copy of itself. A *failed* run keeps its input, because a session write that died
    is exactly what a failure looks like from the log.
    """
    abandoned = {event.run_id for event in history if isinstance(event.payload, RunCancelled)}
    transcript: list[Message] = []
    for event in history:
        payload = event.payload
        if isinstance(payload, RunStarted) and event.run_id in abandoned:
            continue
        if isinstance(payload, RunStarted | InputAppended):
            transcript.append(("user", _text_of(payload.input)))
        elif isinstance(payload, MessageCompleted):
            transcript.append(("assistant", payload.text))
    return transcript


def _session_transcript(items: Sequence[TResponseInputItem]) -> list[Message]:
    """The same view of the session: user and assistant messages only, tool calls,
    tool results and reasoning items skipped."""
    transcript: list[Message] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        if role == "user" or role == "assistant":  # noqa: PLR1714 — two comparisons narrow role, `in` does not
            transcript.append((role, _item_text(item.get("content"))))
    return transcript


def _item_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return ""


def _text_of(input: Input) -> str:
    # Joined exactly the way the engine joins a turn's input on the way in, so a replayed
    # message is the same string the session would have held had the write landed.
    return "\n".join(block.text for block in input if isinstance(block, TextBlock))


def _as_item(role: Role, text: str) -> TResponseInputItem:
    return {"role": role, "content": text}


__all__ = ["DIVERGED", "reconcile"]
