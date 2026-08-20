from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import check_docs_impact as docs_impact
import pytest
from check_docs_impact import (
    PageMapping,
    _sources_from_mdx,
    changed_files,
    check_docs_impact,
    impacted_pages,
    load_mappings,
)

if TYPE_CHECKING:
    from pathlib import Path


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True)


def _commit(repo: Path, message: str) -> None:
    _git(
        repo,
        "-c",
        "user.name=Docs Impact Test",
        "-c",
        "user.email=docs-impact@example.invalid",
        "commit",
        "-qm",
        message,
    )


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


def test_renamed_source_reports_both_paths(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    old_path = tmp_path / "agentdeck" / "core" / "old.py"
    old_path.parent.mkdir(parents=True)
    old_path.write_text("OLD = True\n")
    _git(tmp_path, "add", "--", "agentdeck/core/old.py")
    _commit(tmp_path, "base")

    new_path = tmp_path / "agentdeck" / "elsewhere" / "moved.py"
    new_path.parent.mkdir(parents=True)
    old_path.rename(new_path)
    _git(tmp_path, "add", "--", "agentdeck/core/old.py", "agentdeck/elsewhere/moved.py")
    _commit(tmp_path, "rename")

    assert set(changed_files("HEAD~1", "HEAD", repo_root=tmp_path)) == {
        "agentdeck/core/old.py",
        "agentdeck/elsewhere/moved.py",
    }


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


def test_cli_requires_review_again_after_new_commits(monkeypatch: pytest.MonkeyPatch) -> None:
    mapping = PageMapping("docs-site/content/reference/run.mdx", ("agentdeck/core/*.py",))
    monkeypatch.setattr(docs_impact, "load_mappings", lambda: (mapping,))
    monkeypatch.setattr(docs_impact, "changed_files", lambda base, head: ("agentdeck/core/invocable.py",))

    assert docs_impact.main(["--acknowledge-review", "--pr-action", "synchronize"]) == 1
    assert docs_impact.main(["--acknowledge-review", "--pr-action", "edited"]) == 0
