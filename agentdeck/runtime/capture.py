"""Provenance capture  -  who/which session produced an entity, and why.

Formerly imported from the (never-extracted) ``agentdecks_core`` package; the
model is small enough to own here. Two clearly separated owners:

* **Identity**  -  ``session_id`` / ``author_id`` are built by whoever opens the
  run, never supplied by the thing being traced.
* **Role and why**  -  ``actor`` / ``rationale`` belong to the flow that minted
  the entity.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class CaptureActor(StrEnum):
    SYSTEM = "system"
    AGENT = "agent"
    USER = "user"


class Capture(BaseModel):
    session_id: str | None = None
    author_id: str | None = None
    actor: CaptureActor | None = None
    rationale: str | None = None


__all__ = ["Capture", "CaptureActor"]
