"""The public import path for the AgentDeck-native binding: ``deck.expose(Native.http())``.

The implementation lives under ``agentdeck/adapters/bindings/native/`` with every other in-tree
binding (``rulings.md`` 36); this module is what user code names, so ``adapters`` never appears in
it. Importing ``agentdeck.bindings`` alone does not reach it, so a plugin building against the
SPI still pulls in no HTTP dependency.
"""

from agentdeck.adapters.bindings.native.binding import Native

__all__ = ["Native"]
