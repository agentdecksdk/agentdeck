from __future__ import annotations

from typing import TYPE_CHECKING

import check_docs_impact as docs_impact
import pytest
from check_docs_impact import PageMapping, _sources_from_mdx, check_docs_impact, impacted_pages, load_mappings

if TYPE_CHECKING:
    from pathlib import Path


def test_every_documentation_page_has_a_valid_mapping() -> None:
    assert load_mappings()


def test_page_without_docs_sources_metadata_is_rejected(tmp_path: Path) -> None:
    page = tmp_path / "new-page.mdx"
    page.write_text("# New page\n")

    with pytest.raises(ValueError, match="must have exactly one docs_sources block"):
        _sources_from_mdx(page)


def test_source_change_impacts_its_mapped_page() -> None:
    mapping = PageMapping("docs-site/content/reference/run.mdx", ("agentdeck/core/*.py",))

    assert impacted_pages((mapping,), ("agentdeck/core/invocable.py",)) == (mapping,)


def test_unrelated_change_has_no_documentation_impact() -> None:
    mapping = PageMapping("docs-site/content/reference/run.mdx", ("agentdeck/core/*.py",))

    assert impacted_pages((mapping,), ("tests/test_deck.py",)) == ()


def test_affected_page_must_change_with_its_source() -> None:
    mapping = PageMapping("docs-site/content/reference/run.mdx", ("agentdeck/core/*.py",))

    assert check_docs_impact((mapping,), ("agentdeck/core/invocable.py",)) == (mapping,)
    assert (
        check_docs_impact(
            (mapping,),
            ("agentdeck/core/invocable.py", "docs-site/content/reference/run.mdx"),
        )
        == ()
    )


def test_duplicate_page_mapping_is_rejected() -> None:
    mapping = PageMapping("docs-site/content/reference/run.mdx", ("agentdeck/core/*.py",))

    with pytest.raises(ValueError, match="duplicate documentation mappings"):
        from check_docs_impact import validate_mappings

        validate_mappings((mapping, mapping))


def test_cli_fails_when_an_affected_page_was_not_updated(monkeypatch: pytest.MonkeyPatch) -> None:
    mapping = PageMapping("docs-site/content/reference/run.mdx", ("agentdeck/core/*.py",))
    monkeypatch.setattr(docs_impact, "load_mappings", lambda: (mapping,))
    monkeypatch.setattr(docs_impact, "changed_files", lambda base, head: ("agentdeck/core/invocable.py",))

    assert docs_impact.main([]) == 1


def test_cli_accepts_explicit_review_acknowledgement(monkeypatch: pytest.MonkeyPatch) -> None:
    mapping = PageMapping("docs-site/content/reference/run.mdx", ("agentdeck/core/*.py",))
    monkeypatch.setattr(docs_impact, "load_mappings", lambda: (mapping,))
    monkeypatch.setattr(docs_impact, "changed_files", lambda base, head: ("agentdeck/core/invocable.py",))

    assert docs_impact.main(["--acknowledge-review"]) == 0
