"""Stream-event → canonical-payload translation.

Ported from the delta-only extraction in ``agents/runners/headless.py`` (v1 stays
untouched) and widened to text deltas, completed messages, tool calls and their results.
Reasoning items are deliberately not translated (ADR-D5: they live only in the SDK
session, and mirroring them would chain the event schema to the SDK's item format).
Handoffs are not a core kind (D10: engines translate or namespace, never mint), so a
completed handoff becomes one namespaced ``custom`` event.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from agentdeck.core.events import (
    RESULT_PREVIEW_MAX,
    Custom,
    KnownPayload,
    MessageCompleted,
    TextDelta,
    ToolCallCompleted,
    ToolCallStarted,
)

logger = logging.getLogger(__name__)


def translate(event: Any, tool_names: dict[str, str], tool_failures: dict[str, str]) -> KnownPayload | None:
    """One stream event → one payload, or ``None`` for anything M0 doesn't surface.

    ``tool_names`` is the run's own ``call_id`` → tool-name map: ``tool.call.completed``
    doesn't carry the name, so it is remembered from the paired ``tool.call.started``.
    ``tool_failures`` is the same run's ``call_id`` → formatted-exception map, written by
    ``compile_tool``'s failure formatter (#250) and read back here, once, per call.
    """
    if event.type == "raw_response_event":
        return _translate_raw(event.data)
    if event.type != "run_item_stream_event":
        return None  # agent_updated_stream_event: bookkeeping only, no payload
    return _translate_item(event.item, tool_names, tool_failures)


def _translate_raw(data: Any) -> KnownPayload | None:
    if getattr(data, "type", None) != "response.output_text.delta":
        return None  # every other raw SDK event (created/completed/reasoning/...) is noise here
    return TextDelta(message_id=data.item_id, text=data.delta)


def _translate_item(item: Any, tool_names: dict[str, str], tool_failures: dict[str, str]) -> KnownPayload | None:
    kind = item.type
    if kind == "tool_call_item":
        return _tool_call_started(item, tool_names)
    if kind == "tool_call_output_item":
        return _tool_call_completed(item, tool_names, tool_failures)
    if kind == "message_output_item":
        return _message_completed(item)
    if kind == "handoff_output_item":
        return _handoff(item)
    # handoff_call_item (paired with handoff_output_item, nothing new to say), reasoning
    # items, MCP approval items, computer-use items: not surfaced at M0.
    return None


def _tool_call_started(item: Any, tool_names: dict[str, str]) -> KnownPayload | None:
    raw = item.raw_item
    call_id, name = getattr(raw, "call_id", None), getattr(raw, "name", None)
    if call_id is None or name is None:
        return None  # non-function tool call (e.g. computer use) — out of scope for M0
    tool_names[call_id] = name
    args = _parse_args(getattr(raw, "arguments", None))
    if "_raw" in args:
        # Content-free by design: call_id and tool name only — the raw arguments string
        # could carry user content and must never reach a log line.
        logger.warning("tool call %s (%s) had non-dict JSON args, degraded to _raw", call_id, name)
    return ToolCallStarted(call_id=call_id, tool=name, args=args)


def _parse_args(arguments: str | None) -> dict[str, Any]:
    """Malformed model-emitted JSON degrades to a raw string field rather than killing the
    run over a cosmetic payload attribute — the tool call itself still happened and is
    still logged."""
    if not arguments:
        return {}
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        return {"_raw": arguments}
    return parsed if isinstance(parsed, dict) else {"_raw": arguments}


def _tool_call_completed(item: Any, tool_names: dict[str, str], tool_failures: dict[str, str]) -> KnownPayload:
    call_id = item.call_id or ""
    result = str(item.output)
    encoded = result.encode()
    # `.pop`, not `.get`: a call_id is read back at most once, so a long run's failures don't
    # sit in this dict for its whole lifetime. Capped the same way result_preview is, for the
    # same reason — a traceback can carry whatever the failing call's arguments carried.
    error = tool_failures.pop(call_id, None)
    # TODO(#52): a non-function tool call (computer-use, MCP approval) never populates
    # tool_names via _tool_call_started (that function returns None for it), so its
    # result lands here as an orphan tool.call.completed with tool="unknown" and no
    # paired .started — out of scope for M0's function-tool-only UC1.
    return ToolCallCompleted(
        call_id=call_id,
        tool=tool_names.get(call_id, "unknown"),
        result_preview=result[:RESULT_PREVIEW_MAX],
        result_size=len(encoded),
        result_sha256=hashlib.sha256(encoded).hexdigest(),
        error=error[:RESULT_PREVIEW_MAX] if error is not None else None,
    )


def _message_completed(item: Any) -> KnownPayload:
    raw = item.raw_item
    text = "".join(getattr(part, "text", "") for part in getattr(raw, "content", ()))
    return MessageCompleted(message_id=raw.id, text=text)


def _handoff(item: Any) -> KnownPayload:
    # ADR-D5's invariant ("everything that enters or leaves execution state is recorded")
    # without minting a kind (D10): one namespaced custom event, not a new payload class.
    return Custom(
        name="openai_agents.handoff",
        data={"from": item.source_agent.name, "to": item.target_agent.name},
    )


__all__ = ["translate"]
