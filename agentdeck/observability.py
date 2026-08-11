"""``Langfuse`` — the tracing capability a ``Deck`` composes.

Declared where the deck is declared and owned by the deck's lifecycle, like every other
capability object::

    from agentdeck import Deck
    from agentdeck.observability import Langfuse

    deck = Deck(agents=[booking], observability=Langfuse())

    async with deck:          # tracing starts here, once, before any run
        await deck.run(...)   # never mid-run

Construction reads nothing and builds nothing; :meth:`Langfuse.build` resolves and validates
the configuration without touching the network or importing the SDK, and :meth:`Langfuse.open`
is the one place in the package a Langfuse client is constructed. That single construction
point is not a tidiness preference — see :func:`~agentdeck.adapters.telemetry.langfuse.client.build_client`
for what the SDK does with a second one.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from agentdeck.errors import ConfigError
from agentdeck.runtime.settings import get_settings

if TYPE_CHECKING:
    from agentdeck.core.ports import EventSinkPort
    from agentdeck.runtime.settings import LangfuseSettings


class Langfuse:
    """Langfuse tracing for one ``Deck``.

    Every field defaults to the matching ``AGENTDECK_LANGFUSE_*`` setting, so a project that
    already configures Langfuse through the environment declares it with a bare ``Langfuse()``
    and a project that would rather say it in code passes the values here.

    ``client=`` hands in an already-built Langfuse SDK client instead. It is then the caller's
    to shut down — the ownership rule this Deck applies to every other resource — and the
    configuration arguments are refused alongside it, since a built client cannot be
    reconfigured (see :func:`~agentdeck.adapters.telemetry.langfuse.client.build_client`).
    """

    def __init__(
        self,
        *,
        public_key: str | None = None,
        secret_key: str | None = None,
        base_url: str | None = None,
        environment: str | None = None,
        debug: bool | None = None,
        sample_rate: float | None = None,
        service_name: str | None = None,
        client: Any = None,
    ) -> None:
        overrides = {
            "public_key": public_key,
            "secret_key": secret_key,
            "base_url": base_url,
            "environment": environment,
            "debug": debug,
            "sample_rate": sample_rate,
            "service_name": service_name,
        }
        named = sorted(key for key, value in overrides.items() if value is not None)
        if client is not None and named:
            raise ConfigError(
                f"Langfuse(client=...) was given {named} as well, but a client that already exists "
                "cannot be reconfigured — the Langfuse SDK caches one client per public key and "
                "ignores a later constructor's arguments. Configure the client you build, or drop "
                "client= and let this Deck build one."
            )
        self._overrides = {key: value for key, value in overrides.items() if value is not None}
        self._client = client
        self._owns_client = client is None
        self._settings: LangfuseSettings | None = None

    def build(self) -> LangfuseSettings:
        """Resolve the configuration and check it is usable. No network, no SDK, no client.

        Declaring ``observability=Langfuse()`` with no keys anywhere is a configuration error
        rather than a silent fallback to tracing-off: the argument is the declaration, and a
        deck that accepts it and then exports nothing is the ghost declaration #186 refused
        everywhere else. A deck that wants tracing off omits the argument.
        """
        settings = get_settings().langfuse.with_overrides(**self._overrides)
        if self._client is None and not settings.enabled:
            raise ConfigError(
                "observability=Langfuse(...) is declared, but no Langfuse keys are configured. Set "
                "AGENTDECK_LANGFUSE_PUBLIC_KEY and AGENTDECK_LANGFUSE_SECRET_KEY, pass public_key=/"
                "secret_key= here, or hand in a client with Langfuse(client=...). Omit observability= "
                "to run with tracing off."
            )
        self._settings = settings
        return settings

    def open(self) -> EventSinkPort:
        """Start tracing and hand back the sink to register with the Runtime.

        Called once, by ``Deck.__aenter__``, before any run — this is where the client is
        constructed and the SDK is first imported.
        """
        from agentdeck.adapters.telemetry.langfuse.client import LangfuseTracer, build_client
        from agentdeck.adapters.telemetry.langfuse.sink import LangfuseSink

        settings = self._settings if self._settings is not None else self.build()
        if self._client is None:
            self._client = build_client(settings)
        return LangfuseSink(LangfuseTracer(self._client))

    async def aclose(self) -> None:
        """Shut a client this object constructed down; leave one that was handed in alone.

        On a worker thread for the reason the sink flushes on one: the SDK's shutdown flushes
        its buffer and joins its consumer threads, both of which block.

        A shut-down client stays in the SDK's per-public-key cache, so a *second* Deck opened
        later in the same process with the same key would be handed the dead one back. That is
        what ``Langfuse(client=...)`` is for in a script that opens decks one after another:
        build the client once, hand it to each deck, and shut it down yourself at the end.
        """
        if self._owns_client and self._client is not None:
            client, self._client = self._client, None
            await asyncio.to_thread(client.shutdown)


__all__ = ["Langfuse"]
