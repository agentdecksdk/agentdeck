"""`DeckGateway`'s structural boundary (#599): no attribute on the gateway itself is or holds a
`Deck` (a bound method's `__self__` chain can still reach one), and the generic
`agentdeck.adapters.bindings` import contract catches a new binding with no per-binding entry.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from agentdeck.authoring import Agent
from agentdeck.bindings import DeckGateway
from agentdeck.deck import Deck

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _deck() -> Deck:
    return Deck(agents=[Agent(name="Greeter", instructions="Greet the user.")])


def test_gateway_holds_no_reference_to_the_deck():
    deck = _deck()
    gateway = DeckGateway(deck)

    held = vars(gateway).values()
    assert not any(value is deck for value in held)
    assert not any(isinstance(value, Deck) for value in held)


def test_a_new_binding_module_importing_deck_is_rejected_with_no_per_binding_contract(tmp_path):
    """`binding-implementations-are-spi-facing-only` names the parent package once
    (`.importlinter`): a binding under `adapters/bindings/<name>/` nobody wrote a contract for is
    still caught. Runs the real root config, verbatim, over a copy of the package tree plus one
    rogue module that imports `agentdeck.deck`.
    """
    shutil.copytree(_REPO_ROOT / "agentdeck", tmp_path / "agentdeck", ignore=shutil.ignore_patterns("__pycache__"))
    rogue = tmp_path / "agentdeck" / "adapters" / "bindings" / "rogue"
    rogue.mkdir()
    (rogue / "__init__.py").write_text("")
    (rogue / "binding.py").write_text("from agentdeck.deck import Deck\n")

    lint_imports = Path(sys.executable).with_name("lint-imports")
    result = subprocess.run(
        [
            str(lint_imports),
            "--config",
            str(_REPO_ROOT / ".importlinter"),
            "--contract",
            "binding-implementations-are-spi-facing-only",
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode != 0
    assert "agentdeck.adapters.bindings.rogue.binding -> agentdeck.deck" in result.stdout
