"""``Terminal.stdio()``: the terminal surface (``docs/design/protocols/rulings.md`` 35). Users
import it from ``agentdeck.bindings.terminal``; this package is the implementation.
"""

from agentdeck.adapters.bindings.terminal.binding import Terminal

__all__ = ["Terminal"]
