"""Where an invocable's tools come from: MCP servers today, HTTP or plain functions later.

The port answers one question — *what does this invocable get to call, and what is it not
getting* — so a caller assembling a run never learns which source the tools came from, nor
whether one of them is down.

Connecting and closing a source is **not** on this port: the composition root owns that
(``App`` connects MCP in its lifespan), so resolving tools never connects a source — no
half-open connection appears behind an engine's back, and no run waits on a handshake.
Resolving is not free, though: a source is free to read configuration, so treat it as
startup-shaped work, not as something to call per turn.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from agentdeck.core.content import CoreModel

if TYPE_CHECKING:
    from agentdeck.core.invocable import InvocableSpec


class ToolSet(CoreModel):
    """One source's answer for one invocable: what it yielded, and what it could not.

    ``tools`` are engine-native handles — an MCP server object, an SDK function tool — so
    the ring that consumes them is the one that knows how to attach them. ``unavailable``
    names what the invocable asked for and did not get; ``notice`` is prose to put in front
    of the model when that happens, so a degraded run reports the gap instead of quietly
    answering without the tools.

    ``notice`` is empty whenever nothing is missing: the happy-path prompt has to stay
    byte-identical or every upstream prompt cache misses.
    """

    tools: tuple[Any, ...] = ()
    unavailable: tuple[str, ...] = ()
    notice: str = ""


class ToolSourcePort(ABC):
    """One source of tools.

    Synchronous on purpose: a source resolves from state the composition root already
    connected, and prompt assembly — the caller — is synchronous. A source that would need
    to await here is a source that is doing its connecting too late.

    Never raises for an unreachable or unconfigured source: an empty
    :class:`ToolSet` (or one naming what is missing) is the answer, because a tool source
    being down must degrade a run, not fail it.
    """

    @abstractmethod
    def resolve(self, spec: InvocableSpec) -> ToolSet:
        """The tools this source has for ``spec``, plus whatever it could not supply."""


__all__ = ["ToolSet", "ToolSourcePort"]
