"""``Native.http()``: the AgentDeck protocol, the SPI's own reference implementation
(``docs/design/protocols/native-wire.md``, ``rulings.md`` 18).
"""

from agentdeck.adapters.bindings.native.binding import Native, NativeBinding

__all__ = ["Native", "NativeBinding"]
