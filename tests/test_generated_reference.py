"""Regenerate-and-diff for every generated docs artifact: a generated page that can silently
drift from the source it was generated from is worse than a hand-written one, because nobody
suspects it. Run `python scripts/generate_docs_reference.py` to refresh the committed files after
changing `agentdeck/runtime/settings.py`, `agentdeck/cli.py`, `CHANGELOG.md` or any site page,
and this suite fails loudly if that step was skipped.

Parametrised over `_generated_pages()` rather than one test per artifact, because naming them
individually is what let this gate rot: the suite covered `settings.mdx` and `cli.mdx`, three more
generated files were added in #257 and #260, and none of them arrived with a check. `llms-full.txt`
then shipped in v3.0.1 carrying 30 KB of changelog the site page no longer contained — 18% of the
LLM-facing corpus, stale, in the one file whose whole job is being machine-readable truth. A new
entry in that dict is now covered the moment it exists.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from generate_docs_reference import REPO_ROOT, _generated_pages

_REGEN_HINT = "run `python scripts/generate_docs_reference.py` to regenerate it"
_PAGES = sorted(_generated_pages().items())


@pytest.mark.parametrize("page,expected", _PAGES, ids=lambda v: v.name if isinstance(v, Path) else "")
def test_generated_page_matches_the_generator(page: Path, expected: str) -> None:
    assert page.read_text() == expected, f"{page.relative_to(REPO_ROOT)} is stale — {_REGEN_HINT}"


def test_regenerating_twice_changes_nothing() -> None:
    """The generator must reach a fixed point in one run.

    It does not read only from source: `llms-full.txt` is built by reading the site's `.mdx` files
    off disk, and `changelog.mdx` is itself one of them. So a run renders the new changelog, then
    inlines the *old* one still on disk, then writes both — leaving the corpus one run behind and
    self-consistent, which is exactly why the per-page checks above passed while it was wrong.

    Comparing the dict to a second evaluation catches that: after the first run's writes, the
    second evaluation sees the new changelog and disagrees.
    """
    stale = {page.relative_to(REPO_ROOT) for page, content in _generated_pages().items() if page.read_text() != content}
    assert not stale, f"generator is not at a fixed point — {_REGEN_HINT}, twice: {sorted(map(str, stale))}"
