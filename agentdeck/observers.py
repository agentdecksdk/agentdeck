"""The observers a ``Deck`` can be given — read-only taps on its event stream.

``Deck(observers=[...])`` takes any :class:`~agentdeck.core.ports.EventSinkPort`; this module
holds the one agentdeck ships. :class:`Langfuse` renders each run as a Langfuse trace, built
from the canonical event log rather than from anything the engines do, which is why an agent
turn and a workflow run are traced by the same code.

Configuration is ``AGENTDECK_LANGFUSE_*`` and nothing else — the settings model already layers
init/env/YAML for every knob (endpoint, environment, sample rate, service name), and a second
spelling here would only be a second place to look.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentdeck.core.ports import EventSinkPort
from agentdeck.errors import ConfigError
from agentdeck.runtime.settings import get_settings

if TYPE_CHECKING:
    from agentdeck.core.events import Event


class Langfuse(EventSinkPort):
    """Langfuse tracing for one ``Deck``, configured by ``AGENTDECK_LANGFUSE_*``.

    Constructing this reads nothing and opens nothing. :meth:`start` — which the Deck calls
    once, as it opens, before any run — is where the settings are read, the SDK is first
    imported and the client is built. That single construction point is not tidiness: the SDK
    caches one client per public key and discards every later constructor's arguments (see
    :func:`~agentdeck.adapters.telemetry.langfuse.client.build_client`), so a design with two
    construction sites cannot say which one's configuration is live. Here there is only one.
    """

    def __init__(self) -> None:
        self._sink: EventSinkPort | None = None

    async def start(self) -> None:
        """Build the client and the sink over it. Raises if Langfuse is not configured.

        Naming this observer and getting silence back is the ghost declaration this project
        refuses everywhere else — a Deck that accepted ``observers=[Langfuse()]`` and then
        exported nothing would be worse than one that said so. A Deck that should not trace
        leaves the observer out.
        """
        from agentdeck.adapters.telemetry.langfuse.client import langfuse_sink

        settings = get_settings().langfuse
        if not settings.enabled:
            raise ConfigError(
                "observers=[Langfuse()] was declared, but no Langfuse keys are configured — set "
                "AGENTDECK_LANGFUSE_PUBLIC_KEY and AGENTDECK_LANGFUSE_SECRET_KEY (see "
                "AGENTDECK_LANGFUSE_* for the endpoint, environment and sample rate). Leave the "
                "observer out to run without tracing."
            )
        self._sink = langfuse_sink(settings)

    async def emit(self, event: Event) -> None:
        # Unstarted is not an error state to report per event: the Deck starts every observer
        # before a run can reach one, so the only way here is a caller driving a Runtime by
        # hand, and a dropped span is not worth failing that caller's dispatch over.
        if self._sink is not None:
            await self._sink.emit(event)

    async def close(self) -> None:
        if self._sink is not None:
            await self._sink.close()


__all__ = ["Langfuse"]
