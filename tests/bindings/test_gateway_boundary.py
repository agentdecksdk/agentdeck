"""`DeckGateway`'s structural boundary (#599): no attribute on the gateway itself is or holds a
`Deck` (a bound method's `__self__` chain can still reach one).
"""

from __future__ import annotations

from agentdeck.authoring import Agent
from agentdeck.bindings import DeckGateway
from agentdeck.deck import Deck


def _deck() -> Deck:
    return Deck(agents=[Agent(name="Greeter", instructions="Greet the user.")])


def test_gateway_holds_no_reference_to_the_deck():
    deck = _deck()
    gateway = DeckGateway(deck)

    held = vars(gateway).values()
    assert not any(value is deck for value in held)
    assert not any(isinstance(value, Deck) for value in held)
