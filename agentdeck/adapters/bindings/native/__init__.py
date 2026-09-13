"""``Native.http()``: the AgentDeck protocol, the SPI's own reference implementation
(``docs/design/protocols/native-wire.md``, ``rulings.md`` 18). Users import it from
``agentdeck.bindings.native``; this package is the implementation.
"""

from agentdeck.adapters.bindings.native.binding import Native

__all__ = ["Native"]
