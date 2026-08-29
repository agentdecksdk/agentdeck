"""Regenerate-and-diff for every generated reference page (`docs-site/content/reference/
{settings,cli}.mdx`, `docs-site/content/changelog.mdx`, `docs-site/public/{llms.txt,
llms-full.txt}`, `docs-site/app/generated-version.ts`): a generated page that can silently drift
from the source it was generated from is worse than a hand-written one, because nobody suspects
it. Run `python scripts/generate_docs_reference.py` to refresh the committed pages after changing
`agentdeck/runtime/settings.py`, `agentdeck/cli.py`, `CHANGELOG.md`, or any `docs-site/content/**/*.mdx`
page, and this suite fails loudly if that step was skipped.

Four of the six are pinned byte for byte: `settings.mdx`, `cli.mdx`, `llms.txt` and
`generated-version.ts`. The first three derive from `agentdeck/runtime/settings.py`,
`agentdeck/cli.py` and the set of docs pages, none of which two PRs edit at once by accident.
`generated-version.ts` derives from `CHANGELOG.md`'s newest *released* heading, which only a
release-cut commit adds  -  never two concurrent PRs at once, unlike an `## [Unreleased]` entry.

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

from typing import TYPE_CHECKING

import generate_docs_reference as reference
from generate_docs_reference import (
    CHANGELOG_PAGE,
    CLI_PAGE,
    LLMS_FULL_PAGE,
    LLMS_PAGE,
    SETTINGS_PAGE,
    VERSION_PAGE,
    render_changelog_mdx,
    render_cli_mdx,
    render_generated_version_ts,
    render_llms_full_txt,
    render_llms_txt,
    render_settings_mdx,
)

if TYPE_CHECKING:
    from pytest import MonkeyPatch

_REGEN_HINT = "run `python scripts/generate_docs_reference.py` to regenerate it"


def test_settings_reference_page_matches_the_generator() -> None:
    assert SETTINGS_PAGE.read_text() == render_settings_mdx(), f"{SETTINGS_PAGE} is stale  -  {_REGEN_HINT}"


def test_cli_reference_page_matches_the_generator() -> None:
    assert CLI_PAGE.read_text() == render_cli_mdx(), f"{CLI_PAGE} is stale  -  {_REGEN_HINT}"


def test_llms_txt_matches_the_generator() -> None:
    assert LLMS_PAGE.read_text() == render_llms_txt(), f"{LLMS_PAGE} is stale  -  {_REGEN_HINT}"


def test_version_page_matches_the_generator() -> None:
    assert VERSION_PAGE.read_text() == render_generated_version_ts(), f"{VERSION_PAGE} is stale  -  {_REGEN_HINT}"


def test_the_changelog_page_can_be_regenerated() -> None:
    """Not byte-equal: see the module docstring. `CHANGELOG.md` is union-merged, so pinning this
    would fail every open PR the moment any other one merges an entry."""
    rendered = render_changelog_mdx()
    assert rendered.startswith("---"), "the changelog page lost its frontmatter"
    assert CHANGELOG_PAGE.exists(), f"{CHANGELOG_PAGE} is missing  -  {_REGEN_HINT}"


def test_llms_full_txt_can_be_regenerated() -> None:
    """Not byte-equal, for the same reason: it embeds the changelog."""
    rendered = render_llms_full_txt()
    assert rendered.strip(), "llms-full.txt rendered empty"
    assert "docs_sources" not in rendered, "docs impact metadata leaked into the LLM export"
    assert LLMS_FULL_PAGE.exists(), f"{LLMS_FULL_PAGE} is missing  -  {_REGEN_HINT}"


def test_aggregate_pages_use_content_from_the_same_generator_pass(monkeypatch: MonkeyPatch) -> None:
    marker = "same-pass-changelog-marker"
    monkeypatch.setattr(
        reference,
        "render_changelog_mdx",
        lambda: f"---\ntitle: Changelog\ndescription: Notes.\n---\n\n{marker}\n",
    )

    pages = reference._generated_pages()

    assert marker in pages[LLMS_FULL_PAGE]
