"""Regenerate-and-diff for the two generated reference pages (`docs-site/content/reference/
{settings,cli}.mdx`): a generated page that can silently drift from the code it was generated
from is worse than a hand-written one, because nobody suspects it. Run
`python scripts/generate_docs_reference.py` to refresh the committed pages after changing
`agentdeck/runtime/settings.py` or `agentdeck/cli.py`, and this suite fails loudly if that step
was skipped.
"""

from __future__ import annotations

from generate_docs_reference import CLI_PAGE, SETTINGS_PAGE, render_cli_mdx, render_settings_mdx

_REGEN_HINT = "run `python scripts/generate_docs_reference.py` to regenerate it"


def test_settings_reference_page_matches_the_generator() -> None:
    assert SETTINGS_PAGE.read_text() == render_settings_mdx(), f"{SETTINGS_PAGE} is stale — {_REGEN_HINT}"


def test_cli_reference_page_matches_the_generator() -> None:
    assert CLI_PAGE.read_text() == render_cli_mdx(), f"{CLI_PAGE} is stale — {_REGEN_HINT}"
