"""The public import path for the terminal surface: ``from agentdeck.bindings import
Terminal``, then ``deck.serve(Terminal.stdio())``.

The implementation lives under ``agentdeck/adapters/bindings/terminal/`` with every other in-tree
binding (``rulings.md`` 36); this module is what user code names, so ``adapters`` never appears
in it.
"""

from agentdeck.adapters.bindings.terminal.binding import Terminal

__all__ = ["Terminal"]
