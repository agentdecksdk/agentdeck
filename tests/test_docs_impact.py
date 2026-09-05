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


def test_cli_fails_when_an_affected_page_was_not_updated(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mapping = PageMapping("docs-site/content/reference/run.mdx", ("agentdeck/core/*.py",))
    monkeypatch.setattr(docs_impact, "load_mappings", lambda: (mapping,))
    monkeypatch.setattr(docs_impact, "changed_files", lambda base, head: ("agentdeck/core/invocable.py",))
    monkeypatch.delenv("PR_BODY", raising=False)

    assert docs_impact.main([]) == 1
    err = capsys.readouterr().err
    # The verbatim line to paste, not just the page name (#719: naming pages alone cost a review round).
    assert "- [x] Unchanged pages reviewed: reference/run.mdx" in err
    assert "gh run rerun" in err


def test_an_acknowledgement_with_no_page_list_still_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    mapping = PageMapping("docs-site/content/reference/run.mdx", ("agentdeck/core/*.py",))
    monkeypatch.setattr(docs_impact, "load_mappings", lambda: (mapping,))
    monkeypatch.setattr(docs_impact, "changed_files", lambda base, head: ("agentdeck/core/invocable.py",))
    # The words are there, but no pages named: the accepting `\s*` must not turn this into a match.
    monkeypatch.setenv("PR_BODY", "- [x] Unchanged pages reviewed:\n")

    assert docs_impact.main([]) == 1


def test_naming_the_affected_page_acknowledges_it(monkeypatch: pytest.MonkeyPatch) -> None:
    mapping = PageMapping("docs-site/content/reference/run.mdx", ("agentdeck/core/*.py",))
    monkeypatch.setattr(docs_impact, "load_mappings", lambda: (mapping,))
    monkeypatch.setattr(docs_impact, "changed_files", lambda base, head: ("agentdeck/core/invocable.py",))
    # A real GitHub body, CRLF and all: the last name carries a trailing \r out of the match.
    monkeypatch.setenv("PR_BODY", "## Design\r\n\r\n- [x] Unchanged pages reviewed: reference/run.mdx\r\n")

    assert docs_impact.main([]) == 0


def test_a_non_whitespace_prefix_is_not_an_indent() -> None:
    # `^\s*` accepts indentation, not any prefix: a quote marker or stray text must not count.
    assert docs_impact.ACKNOWLEDGEMENT.search("> - [x] Unchanged pages reviewed: foo.mdx") is None


def test_an_empty_page_list_does_not_match_at_all() -> None:
    # `(?P<pages>.+)` requires at least one page, asserted at the regex itself: downstream
    # page.strip() filtering would otherwise mask a loosened `.*` from ever being noticed.
    assert docs_impact.ACKNOWLEDGEMENT.search("- [x] Unchanged pages reviewed:\n") is None


def test_an_indented_acknowledgement_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    mapping = PageMapping("docs-site/content/reference/run.mdx", ("agentdeck/core/*.py",))
    monkeypatch.setattr(docs_impact, "load_mappings", lambda: (mapping,))
    monkeypatch.setattr(docs_impact, "changed_files", lambda base, head: ("agentdeck/core/invocable.py",))
    # A list-continuation indent, which Markdown renders identically to column 0 (#719).
    monkeypatch.setenv("PR_BODY", "  - [x] Unchanged pages reviewed: reference/run.mdx")

    assert docs_impact.main([]) == 0


def test_acknowledgement_survives_a_push_that_affects_nothing_new(monkeypatch: pytest.MonkeyPatch) -> None:
    mapping = PageMapping("docs-site/content/reference/run.mdx", ("agentdeck/core/*.py",))
    monkeypatch.setattr(docs_impact, "load_mappings", lambda: (mapping,))
    monkeypatch.setenv("PR_BODY", "- [x] Unchanged pages reviewed: docs-site/content/reference/run.mdx")

    monkeypatch.setattr(docs_impact, "changed_files", lambda base, head: ("agentdeck/core/invocable.py",))
    assert docs_impact.main([]) == 0
    monkeypatch.setattr(
        docs_impact,
        "changed_files",
        lambda base, head: ("agentdeck/core/invocable.py", "agentdeck/core/context.py"),
    )
    assert docs_impact.main([]) == 0


def test_a_newly_affected_page_expires_the_acknowledgement(monkeypatch: pytest.MonkeyPatch) -> None:
    run = PageMapping("docs-site/content/reference/run.mdx", ("agentdeck/core/*.py",))
    deck = PageMapping("docs-site/content/reference/deck.mdx", ("agentdeck/deck.py",))
    monkeypatch.setattr(docs_impact, "load_mappings", lambda: (run, deck))
    monkeypatch.setattr(docs_impact, "changed_files", lambda base, head: ("agentdeck/core/invocable.py",))
    monkeypatch.setenv("PR_BODY", "- [x] Unchanged pages reviewed: reference/run.mdx")

    assert docs_impact.main([]) == 0
    monkeypatch.setattr(
        docs_impact,
        "changed_files",
        lambda base, head: ("agentdeck/core/invocable.py", "agentdeck/deck.py"),
    )
    assert docs_impact.main([]) == 1


def test_report_mode_names_the_pages_and_the_line_that_clears_them(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mapping = PageMapping("docs-site/content/reference/run.mdx", ("agentdeck/core/*.py",))
    monkeypatch.setattr(docs_impact, "load_mappings", lambda: (mapping,))
    monkeypatch.setattr(docs_impact, "changed_files", lambda base, head: ("agentdeck/core/invocable.py",))
    monkeypatch.delenv("PR_BODY", raising=False)

    assert docs_impact.main(["--report"]) == 0
    report = capsys.readouterr().out
    assert "docs-site/content/reference/run.mdx" in report
    assert "- [x] Unchanged pages reviewed: reference/run.mdx" in report


def test_report_mode_still_fails_on_a_source_pattern_matching_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> tuple[PageMapping, ...]:
        raise ValueError("source patterns matching no files: run.mdx: agentdeck/gone/*.py")

    monkeypatch.setattr(docs_impact, "load_mappings", broken)

    assert docs_impact.main(["--report"]) == 2


def test_report_mode_survives_a_base_revision_the_clone_does_not_have(monkeypatch: pytest.MonkeyPatch) -> None:
    mapping = PageMapping("docs-site/content/reference/run.mdx", ("agentdeck/core/*.py",))
    monkeypatch.setattr(docs_impact, "load_mappings", lambda: (mapping,))

    def unfetched(base: str, head: str) -> tuple[str, ...]:
        raise subprocess.CalledProcessError(128, ["git", "diff"])

    monkeypatch.setattr(docs_impact, "changed_files", unfetched)

    assert docs_impact.main(["--report"]) == 0
    assert docs_impact.main([]) == 2
