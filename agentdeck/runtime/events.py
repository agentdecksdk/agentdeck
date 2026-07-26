"""Cross-provider shape probes for Agents-SDK streaming events.

The SDK surfaces raw provider items on ``RunItem.raw_item`` whose shape
varies between Responses API, Chat Completions, and LocalShell. This
module is the single boundary that touches those raw shapes — every
helper takes the SDK ``item`` and returns a normalized value, so callers
never reach for ``raw_item`` themselves.
"""

from __future__ import annotations

import json
from typing import Any

from openai.types.responses import ResponseTextDeltaEvent


def _raw(item: Any) -> Any:
    return getattr(item, "raw_item", None)


def text_delta(data: Any) -> str:
    if isinstance(data, ResponseTextDeltaEvent):
        return data.delta
    choices = getattr(data, "choices", None)
    if choices and isinstance(content := getattr(getattr(choices[0], "delta", None), "content", None), str):
        return content
    return ""


def tool_name(item: Any) -> str:
    raw = _raw(item)
    if isinstance(raw, dict) and (name := raw.get("name") or raw.get("type")):
        return str(name)
    name = getattr(item, "tool_name", None) or getattr(raw, "name", None)
    return str(name) if name else "tool"


def tool_args(item: Any) -> str | dict[str, Any]:
    raw = _raw(item)
    if isinstance(raw, dict):
        for key in ("arguments", "command", "cmd"):
            if value := raw.get(key):
                return value
        if isinstance(action := raw.get("action"), dict):
            for key in ("command", "cmd"):
                if isinstance(value := action.get(key), str) and value:
                    return value
    return getattr(raw, "arguments", None) or ""


def tool_args_dict(args: str | dict[str, Any] | None) -> dict[str, Any] | None:
    """Parse a tool's argument payload to a ``dict``, or ``None`` when undecodable.

    Returns ``None`` for empty input, JSON values that aren't objects, and
    strings that don't parse — callers that want the raw fallback should
    inspect the original ``args`` themselves.
    """
    if isinstance(args, dict):
        return args
    if not isinstance(args, str) or not args:
        return None
    try:
        parsed = json.loads(args)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


_PRIMARY_ARG_KEYS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("load_skill", ("skill_name", "name")),
    ("exec_command", ("cmd", "command", "script")),
    ("shell", ("cmd", "command", "script")),
    ("local_shell", ("cmd", "command", "script")),
)


def tool_label(name: str, args: str | dict[str, Any] | None) -> str:
    """Display label like ``load_skill <name>`` or ``exec_command <cmd>``.

    For tools with a well-known primary argument (shell/exec/load_skill),
    the value is inlined. Tools whose args are a single scalar render that
    scalar inline. Everything else renders as the bare tool name.
    """
    parsed = tool_args_dict(args)
    for key, candidates in _PRIMARY_ARG_KEYS:
        if name != key:
            continue
        if parsed:
            for field in candidates:
                if isinstance(value := parsed.get(field), str) and value:
                    return f"{name} {value}"
        return name
    if parsed is None and isinstance(args, str) and args:
        scalar = _scalar_or_none(args)
        if scalar is not None:
            return f"{name} {scalar}"
    return name


def _scalar_or_none(args: str) -> str | None:
    """Return ``args`` as a JSON scalar string, or ``None`` for objects/lists/null."""
    try:
        parsed = json.loads(args)
    except json.JSONDecodeError:
        return args
    if isinstance(parsed, (dict, list)) or parsed is None:
        return None
    return str(parsed)


def clip(text: str, limit: int) -> str:
    """Truncate ``text`` to ``limit`` chars, appending ``…`` when clipped."""
    return text if len(text) <= limit else text[:limit] + "…"


def tool_output_text(item: Any) -> str:
    output = getattr(item, "output", None)
    if isinstance(output, str):
        return output
    if output is not None:
        return str(output)
    raw = _raw(item)
    if isinstance(raw, dict):
        value = raw.get("output") or raw.get("content")
        return value if isinstance(value, str) else (str(value) if value else "")
    return ""


def call_id(item: Any) -> str | None:
    if cid := getattr(item, "call_id", None):
        return str(cid)
    raw = _raw(item)
    if isinstance(raw, dict):
        cid = raw.get("call_id") or raw.get("id")
        return str(cid) if cid else None
    cid = getattr(raw, "call_id", None) or getattr(raw, "id", None)
    return str(cid) if cid else None


def synthetic_key(item: Any) -> int:
    """Stable identity for items lacking a real ``call_id``.

    Multiple events for the same raw payload share the raw object, so its
    ``id()`` collapses them onto one synthetic call id at the call site.
    """
    raw = _raw(item)
    return id(raw) if raw is not None else id(item)


def message_text(item: Any) -> str:
    raw = _raw(item)
    content = getattr(raw, "content", None) or (raw.get("content") if isinstance(raw, dict) else None)
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(
        str(getattr(p, "text", None) or (p.get("text") if isinstance(p, dict) else "") or "") for p in content
    )


__all__ = [
    "call_id",
    "clip",
    "message_text",
    "synthetic_key",
    "text_delta",
    "tool_args",
    "tool_args_dict",
    "tool_label",
    "tool_name",
    "tool_output_text",
]
