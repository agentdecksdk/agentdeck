"""Bringing the SDK session back in line with the event log after a crash between the
two writes (ADR-D5: the log records the intent, the session is the engine's working memory).

A turn writes the log first and the session second, so a process that dies in between
leaves the log holding a message the session never got — a question the model would
otherwise never see, or an answer it would think it never gave. On the next turn this
compares the two message-level transcripts and appends whatever the session is missing.

Message level means content and order, nothing more: the log stores tool results
truncated and never stores reasoning items, so those are not reconstructable and are left
out of both transcripts rather than replayed badly. A session that has gone somewhere the
log's prefix does not cover is left untouched — it is the authority on execution, and a
wrong guess about its tail is worse than a gap.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from agentdeck.core.content import TextBlock
from agentdeck.core.events import InputAppended, MessageCompleted, RunStarted

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agents.items import TResponseInputItem
    from agents.memory.session import Session

    from agentdeck.core.content import Input
    from agentdeck.core.events import Event

logger = logging.getLogger(__name__)

Message = tuple[str, str]
"""One transcript entry: the role that spoke and the text it said."""


async def reconcile(session: Session, history: Sequence[Event]) -> None:
    """Append the messages ``history`` records and ``session`` is missing, before a turn runs."""
    # ponytail: whole log against whole session, every turn — the same ceiling the Runtime's
    # own per-turn log read already has, and the SDK reads the whole session anyway. Both want
    # windowing together, once a session's history outgrows one read.
    logged = _log_transcript(history)
    if not logged:
        return
    stored = _session_transcript(await session.get_items())
    if stored != logged[: len(stored)]:
        # Either the session went somewhere the log never recorded, or it is ahead of the log
        # altogether — both mean the log cannot say what this session's tail should be.
        logger.warning(
            "session %s is not the log's prefix (%d session messages, %d logged): replaying nothing",
            session.session_id,
            len(stored),
            len(logged),
        )
        return
    missing = logged[len(stored) :]
    if not missing:
        return
    logger.info("replaying %d logged message(s) the session is missing into %s", len(missing), session.session_id)
    await session.add_items([_as_item(role, text) for role, text in missing])


def _log_transcript(history: Sequence[Event]) -> list[Message]:
    """Every message the log says entered or left the loop, in order.

    ``run.started`` is where a turn's input lands, ``input.appended`` where mid-turn
    steering does; ``message.completed`` is the assistant's side. Deltas are streaming UX
    and tool traffic is not message level, so neither belongs here.
    """
    transcript: list[Message] = []
    for event in history:
        payload = event.payload
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
        if role not in ("user", "assistant"):
            continue
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


def _as_item(role: str, text: str) -> TResponseInputItem:
    # Spelled out per role rather than passing `role` through: the SDK's item type wants a
    # literal, and a plain message is all a message-level replay is allowed to reconstruct.
    if role == "assistant":
        return {"role": "assistant", "content": text}
    return {"role": "user", "content": text}


__all__ = ["reconcile"]
