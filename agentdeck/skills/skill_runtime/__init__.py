"""The **skill runtime** — the shared library for sandboxed skills.

Source under ``agentdeck/skills/skill_runtime``; a skill script imports it as
``skill_runtime`` from the venv the sandbox subprocess shares.

Skill-facing surface:

* :func:`capture` — the ``SANDBOX_CAPTURE`` identity reader.
"""

from __future__ import annotations

from .capture import CAPTURE_ENV, capture

__all__ = ["CAPTURE_ENV", "capture"]
