"""Release bookkeeping: version bump text transforms, and milestone/issue
bookkeeping against a faked `gh`/git layer (no live GitHub calls)."""

from __future__ import annotations

import json
import subprocess

import pytest
from release_bump import (
    bump_changelog,
    bump_pyproject,
    cmd_close_milestone,
    cmd_issues,
    cmd_promote,
    latest_tag,
    referenced_issues,
    referenced_issues_in_range,
)

PYPROJECT_FIXTURE = '[project]\nname = "agentdeck"\nversion = "5.2.0"\n'

CHANGELOG_FIXTURE = """# Changelog

## [Unreleased]

### Added

- something new (#10)

## [5.2.0] - 2026-08-29

### Added

- old stuff

[Unreleased]: https://github.com/agentdecksdk/agentdeck/compare/v5.2.0...HEAD
[5.2.0]: https://github.com/agentdecksdk/agentdeck/compare/v5.1.0...v5.2.0
"""


def test_bump_pyproject_rewrites_the_version_line() -> None:
    assert 'version = "5.3.0"' in bump_pyproject(PYPROJECT_FIXTURE, "5.3.0")


def test_bump_pyproject_without_a_version_line_is_rejected() -> None:
    with pytest.raises(ValueError, match="no version line"):
        bump_pyproject('[project]\nname = "agentdeck"\n', "5.3.0")


def test_bump_changelog_moves_unreleased_entries_under_a_dated_heading() -> None:
    result = bump_changelog(CHANGELOG_FIXTURE, "5.3.0", "2026-09-01")

    assert "## [Unreleased]\n\n## [5.3.0] - 2026-09-01" in result
    assert "- something new (#10)" in result
    # the old release's own entries and heading are untouched
    assert "## [5.2.0] - 2026-08-29" in result
    assert "- old stuff" in result


def test_bump_changelog_rewrites_both_compare_link_footer_lines() -> None:
    result = bump_changelog(CHANGELOG_FIXTURE, "5.3.0", "2026-09-01")

    assert "[Unreleased]: https://github.com/agentdecksdk/agentdeck/compare/v5.3.0...HEAD" in result
    assert "[5.3.0]: https://github.com/agentdecksdk/agentdeck/compare/v5.2.0...v5.3.0" in result
    assert "[5.2.0]: https://github.com/agentdecksdk/agentdeck/compare/v5.1.0...v5.2.0" in result


def test_bump_changelog_with_empty_unreleased_is_rejected() -> None:
    empty = CHANGELOG_FIXTURE.replace("### Added\n\n- something new (#10)\n\n", "")
    with pytest.raises(ValueError, match="nothing to release"):
        bump_changelog(empty, "5.3.0", "2026-09-01")


def test_bump_changelog_without_an_unreleased_section_is_rejected() -> None:
    with pytest.raises(ValueError, match="no \\[Unreleased\\] section"):
        bump_changelog("# Changelog\n\n## [5.2.0] - 2026-08-29\n", "5.3.0", "2026-09-01")


def test_referenced_issues_takes_one_reference_per_line() -> None:
    # Mirrors the bug #560 reports: GitHub (and this scan) only recognizes the first
    # reference in a comma-separated list, so #4 here is deliberately not returned.
    log = "Fixes #1\n\nsome body text\nCloses #2\nFixes #3, #4\n"
    assert referenced_issues(log) == [1, 2, 3]


def test_latest_tag_ignores_reachability_from_head() -> None:
    # A release tag lands on `main`; `dev` never merges it back, so the lookup must not depend
    # on tags being ancestors of the current checkout (unlike `git describe --tags`).
    calls: list[list[str]] = []

    def fake_run(args: list[str]) -> str:
        calls.append(args)
        return "v5.2.0\nv5.1.0\nv5.0.3\n"

    assert latest_tag(fake_run) == "v5.2.0"
    assert calls == [["git", "tag", "--list", "v*", "--sort=-v:refname"]]


def test_latest_tag_with_no_tags_is_rejected() -> None:
    with pytest.raises(ValueError, match="no vX.Y.Z tags"):
        latest_tag(lambda args: "")


def test_referenced_issues_in_range_resolves_the_trailing_pr_reference() -> None:
    # The leading (#N) in a squash subject is often the issue the PR closed, not a PR: only
    # the trailing one is asked.
    def fake_run(args: list[str]) -> str:
        if args[:2] == ["git", "log"] and "-1" not in args:
            return "abc\tfix(a): thing (#10) (#100)\n"
        if args[:3] == ["gh", "pr", "view"]:
            assert args[3] == "100"
            return json.dumps({"closingIssuesReferences": [{"number": 10}]})
        raise AssertionError(args)

    assert referenced_issues_in_range("v1.0.0", fake_run) == [10]


def test_referenced_issues_in_range_falls_back_to_prose_for_a_direct_push() -> None:
    def fake_run(args: list[str]) -> str:
        if args[:2] == ["git", "log"] and "-1" not in args:
            return "abc\tchore: direct push\n"
        if args[:3] == ["git", "log", "-1"]:
            return "Fixes #12\n"
        raise AssertionError(args)

    assert referenced_issues_in_range("v1.0.0", fake_run) == [12]


def test_referenced_issues_in_range_a_resolved_pr_with_no_closing_issues_contributes_nothing() -> None:
    def fake_run(args: list[str]) -> str:
        if args[:2] == ["git", "log"] and "-1" not in args:
            return "abc\tchore: bump to 6.0.3 (#700)\n"
        if args[:3] == ["gh", "pr", "view"]:
            assert args[3] == "700"
            return json.dumps({"closingIssuesReferences": []})
        raise AssertionError(args)

    assert referenced_issues_in_range("v1.0.0", fake_run) == []


def test_referenced_issues_in_range_skips_a_trailing_reference_that_is_not_a_real_pr() -> None:
    # gh pr view fails for a number that doesn't resolve to a PR; the scan degrades rather
    # than aborting the whole range.
    def fake_run(args: list[str]) -> str:
        if args[:2] == ["git", "log"] and "-1" not in args:
            return "abc\tfix(a): stray (#999)\n"
        if args[:3] == ["gh", "pr", "view"]:
            raise subprocess.CalledProcessError(1, args)
        raise AssertionError(args)

    assert referenced_issues_in_range("v1.0.0", fake_run) == []


class FakeGh:
    """Records every `run` call and answers from a small fixed script."""

    def __init__(self, milestones: list[dict[str, object]]) -> None:
        self.milestones = milestones
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        if args[:2] == ["git", "tag"]:
            return "v5.2.0\nv5.1.0\n"
        if args[:2] == ["git", "log"] and "-1" not in args:
            return (
                "h1\tfix(a): thing (#10) (#100)\n"
                "h2\tfix(b): other (#11) (#101)\n"
                "h3\tfix(c): stray (#999)\n"
                "h4\tchore: direct push\n"
            )
        if args[:3] == ["git", "log", "-1"]:
            return {"h4": "Fixes #12\n"}[args[-1]]
        if args[:2] == ["gh", "pr"] and args[2] == "view":
            pr = args[3]
            if pr not in ("100", "101"):
                raise subprocess.CalledProcessError(1, args)
            return json.dumps({"closingIssuesReferences": [{"number": {"100": 10, "101": 11}[pr]}]})
        if args[:2] == ["gh", "api"] and "--method" in args and args[args.index("--method") + 1] == "GET":
            return json.dumps(self.milestones)
        if args[:2] == ["gh", "api"] and "--method" not in args:
            self.milestones.append({"title": args[-1].removeprefix("title="), "number": 99})
            return json.dumps({"number": 99})
        if args[:3] == ["gh", "issue", "edit"]:
            return ""
        if args[:3] == ["gh", "issue", "create"]:
            return "https://github.com/agentdecksdk/agentdeck/issues/555\n"
        if args[:2] == ["gh", "api"] and "--method" in args and args[args.index("--method") + 1] == "PATCH":
            return ""
        raise AssertionError(f"unexpected command: {args}")


def test_issues_creates_milestone_assigns_referenced_issues_and_returns_tracking_issue() -> None:
    fake = FakeGh(milestones=[])

    number = cmd_issues("5.3.0", run=fake)

    assert number == 555
    creates = [call for call in fake.calls if call[:2] == ["gh", "api"] and "--method" not in call]
    assert any(call[-1] == "title=v5.3.0" for call in creates)
    edits = [call for call in fake.calls if call[:3] == ["gh", "issue", "edit"]]
    assert {call[3] for call in edits} == {"10", "11", "12"}
    assert all("v5.3.0" in call for call in edits)
    create_issue = next(call for call in fake.calls if call[:3] == ["gh", "issue", "create"])
    assert "chore: cut and ship v5.3.0" in create_issue
    assert "chore" in create_issue
    assert "#10, #11, #12" in create_issue[create_issue.index("--body") + 1]


def test_issues_reuses_an_existing_milestone() -> None:
    fake = FakeGh(milestones=[{"title": "v5.3.0", "number": 7}])

    cmd_issues("5.3.0", run=fake)

    creates = [call for call in fake.calls if call[:2] == ["gh", "api"] and "--method" not in call]
    assert creates == []


def test_promote_creates_a_main_promotion_tracking_issue() -> None:
    fake = FakeGh(milestones=[{"title": "v5.3.0", "number": 7}])

    number = cmd_promote("5.3.0", run=fake)

    assert number == 555
    create_issue = next(call for call in fake.calls if call[:3] == ["gh", "issue", "create"])
    assert "chore: release v5.3.0 to main" in create_issue
    assert "closed by hand" in create_issue[create_issue.index("--body") + 1]


def test_close_milestone_patches_the_matching_milestone_closed() -> None:
    fake = FakeGh(milestones=[{"title": "v5.3.0", "number": 42}])

    cmd_close_milestone("5.3.0", run=fake)

    patch = next(call for call in fake.calls if "--method" in call and "PATCH" in call)
    assert "repos/agentdecksdk/agentdeck/milestones/42" in patch
    assert "state=closed" in patch


def test_close_milestone_without_a_matching_milestone_is_rejected() -> None:
    fake = FakeGh(milestones=[])

    with pytest.raises(SystemExit, match="no milestone named"):
        cmd_close_milestone("5.3.0", run=fake)
