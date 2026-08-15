"""Regenerate-and-diff for every generated reference page (`docs-site/content/reference/
{settings,cli}.mdx`, `docs-site/content/changelog.mdx`, `docs-site/public/{llms.txt,
llms-full.txt}`): a generated page that can silently drift from the source it was generated from
is worse than a hand-written one, because nobody suspects it. Run
`python scripts/generate_docs_reference.py` to refresh the committed pages after changing
`agentdeck/runtime/settings.py`, `agentdeck/cli.py`, `CHANGELOG.md`, or any `docs-site/content/**/*.mdx`
page, and this suite fails loudly if that step was skipped.

All five files `generate_docs_reference.py` writes are pinned here — three of them (the
changelog, `llms.txt`, `llms-full.txt`) previously had no test at all, so a page could drift for
a release before anyone noticed. Reported twice as unrelated churn by agents who regenerated one
page and got surprised by the other four (#317).
"""

from __future__ import annotations

from generate_docs_reference import (
    CHANGELOG_PAGE,
    CLI_PAGE,
    LLMS_FULL_PAGE,
    LLMS_PAGE,
    SETTINGS_PAGE,
    render_changelog_mdx,
    render_cli_mdx,
    render_llms_full_txt,
    render_llms_txt,
    render_settings_mdx,
)

_REGEN_HINT = "run `python scripts/generate_docs_reference.py` to regenerate it"


def test_settings_reference_page_matches_the_generator() -> None:
    assert SETTINGS_PAGE.read_text() == render_settings_mdx(), f"{SETTINGS_PAGE} is stale — {_REGEN_HINT}"


def test_cli_reference_page_matches_the_generator() -> None:
    assert CLI_PAGE.read_text() == render_cli_mdx(), f"{CLI_PAGE} is stale — {_REGEN_HINT}"


def test_changelog_page_matches_the_generator() -> None:
    assert CHANGELOG_PAGE.read_text() == render_changelog_mdx(), f"{CHANGELOG_PAGE} is stale — {_REGEN_HINT}"


def test_llms_txt_matches_the_generator() -> None:
    assert LLMS_PAGE.read_text() == render_llms_txt(), f"{LLMS_PAGE} is stale — {_REGEN_HINT}"


def test_llms_full_txt_matches_the_generator() -> None:
    assert LLMS_FULL_PAGE.read_text() == render_llms_full_txt(), f"{LLMS_FULL_PAGE} is stale — {_REGEN_HINT}"
