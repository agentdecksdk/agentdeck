"""Where an invocable's tools come from: MCP servers today, HTTP or plain functions later.

The port answers one question — *what does this invocable get to call, and what is it not
getting* — so a caller never learns which source the tools came from, nor whether one is down.

Connecting and closing a source belongs to the composition root (``App`` connects MCP in its
lifespan), not here: resolving never opens a connection behind an engine's back and no run
waits on a handshake. It is not free either — a source may read configuration — so treat it as
startup-shaped work, not per-turn work.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from agentdeck.core.content import CoreModel

if TYPE_CHECKING:
    from agentdeck.core.invocable import InvocableSpec


class ToolSet(CoreModel):
    """One source's answer for one invocable: what it yielded, and what it could not.

    ``tools`` are engine-native handles — an MCP server object, an SDK function tool — so the
    ring that consumes them is the one that knows how to attach them. ``notice`` is prose to put
    in front of the model about what is missing, so a degraded run reports the gap instead of
    quietly answering without the tools. It is empty whenever nothing is missing: the happy-path
    prompt has to stay byte-identical or every upstream prompt cache misses.
    """

    tools: tuple[Any, ...] = ()
    unavailable: tuple[str, ...] = ()
    notice: str = ""


class ToolSourcePort(ABC):
    """One source of tools.

    Synchronous on purpose: a source resolves from state the composition root already connected,
    and prompt assembly is synchronous. Needing to await here means connecting too late.

    Never raises for an unreachable or unconfigured source — an empty :class:`ToolSet`, or one
    naming what is missing, is the answer. A tool source being down degrades a run, not fails it.
    """

    @abstractmethod
    def resolve(self, spec: InvocableSpec) -> ToolSet:
        """The tools this source has for ``spec``, plus whatever it could not supply."""
