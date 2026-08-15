"""Regenerate-and-diff for every generated reference page (`docs-site/content/reference/
{settings,cli}.mdx`, `docs-site/content/changelog.mdx`, `docs-site/public/{llms.txt,
llms-full.txt}`): a generated page that can silently drift from the source it was generated from
is worse than a hand-written one, because nobody suspects it. Run
`python scripts/generate_docs_reference.py` to refresh the committed pages after changing
`agentdeck/runtime/settings.py`, `agentdeck/cli.py`, `CHANGELOG.md`, or any `docs-site/content/**/*.mdx`
page, and this suite fails loudly if that step was skipped.

Three of the five are pinned byte for byte: `settings.mdx`, `cli.mdx` and `llms.txt`. They
derive from `agentdeck/runtime/settings.py`, `agentdeck/cli.py` and the set of docs pages, none
of which two PRs edit at once by accident.

**`changelog.mdx` and `llms-full.txt` are asserted regenerable, not byte-equal, and that is
deliberate.** Both derive from `CHANGELOG.md`, which is `merge=union` in `.gitattributes`
precisely so concurrent PRs can each add an entry without conflicting. A byte pin turns that
back into a serialization point: every open PR goes red the moment any other PR merges an entry,
whether or not it touched the changelog itself, and the only fix is to merge `dev` and
regenerate. That trades a rare stale page for constant friction on every branch.

What is still caught: a generator that cannot produce them at all, which is the failure that
would leave the pages frozen at whatever was last committed. Regenerating them at docs-build
time would remove the tradeoff entirely and deserves its own issue.
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


def test_llms_txt_matches_the_generator() -> None:
    assert LLMS_PAGE.read_text() == render_llms_txt(), f"{LLMS_PAGE} is stale — {_REGEN_HINT}"


def test_the_changelog_page_can_be_regenerated() -> None:
    """Not byte-equal: see the module docstring. `CHANGELOG.md` is union-merged, so pinning this
    would fail every open PR the moment any other one merges an entry."""
    rendered = render_changelog_mdx()
    assert rendered.startswith("---"), "the changelog page lost its frontmatter"
    assert CHANGELOG_PAGE.exists(), f"{CHANGELOG_PAGE} is missing — {_REGEN_HINT}"


def test_llms_full_txt_can_be_regenerated() -> None:
    """Not byte-equal, for the same reason: it embeds the changelog."""
    assert render_llms_full_txt().strip(), "llms-full.txt rendered empty"
    assert LLMS_FULL_PAGE.exists(), f"{LLMS_FULL_PAGE} is missing — {_REGEN_HINT}"
