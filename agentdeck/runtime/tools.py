"""Shared JSON envelope builders for host-side tools.

Every host-side tool (agent or pipeline) returns a JSON string with an ``ok``
flag, a ``reason`` on failure, and a free-form payload on success. This is
framework infrastructure — the return contract every catalog bundle's tools
speak — so it lives in :mod:`agentdeck.runtime`, not in ``catalog/`` where the
bundles that consume it live. Sits above :mod:`agentdeck.skills` (``skill_err``
adapts a :class:`~agentdeck.skills.SkillResult`).
"""

from __future__ import annotations

import json
from typing import Any

from agentdeck.skills import SkillResult


def tool_ok(**payload: Any) -> str:
    """Encode an ``ok=true`` JSON envelope; ``payload`` becomes top-level fields."""
    return json.dumps({"ok": True, **payload}, ensure_ascii=False)


def tool_err(reason: str, *, detail: str = "", **payload: Any) -> str:
    """Encode an ``ok=false`` JSON envelope. ``reason`` is the short machine code."""
    envelope: dict[str, Any] = {"ok": False, "reason": reason}
    if detail:
        envelope["detail"] = detail
    envelope.update(payload)
    return json.dumps(envelope, ensure_ascii=False)


def skill_err(reason: str, result: SkillResult) -> str:
    """Encode a skill non-zero exit as a ``tool_err`` envelope with clipped stderr."""
    return tool_err(reason, detail=(result.stderr or "").strip()[:500])


__all__ = ["skill_err", "tool_err", "tool_ok"]
