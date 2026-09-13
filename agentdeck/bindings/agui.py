"""The public import path for the AG-UI binding: ``from agentdeck.bindings import AGUI``,
then ``deck.serve(AGUI.http("/agui"))``.

The implementation lives under ``agentdeck/adapters/bindings/agui/`` with every other in-tree
binding (``rulings.md`` 36); this module is what user code names, so ``adapters`` never appears in
it. Importing ``agentdeck.bindings`` alone does not reach it, so a plugin building against the
SPI still pulls in no ``ag-ui-protocol`` or HTTP dependency.
"""

from agentdeck.adapters.bindings.agui.binding import AGUI

__all__ = ["AGUI"]
