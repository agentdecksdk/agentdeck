"""Required governance checks must run from trusted base-branch definitions."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def workflow_events(name: str) -> dict[str, object]:
    workflow = yaml.load((WORKFLOWS / name).read_text(), Loader=yaml.BaseLoader)
    return workflow["on"]


def test_governance_checks_use_trusted_pull_request_target() -> None:
    events = workflow_events("issue-hygiene.yml")
    workflow = (WORKFLOWS / "issue-hygiene.yml").read_text()
    assert "pull_request_target" in events
    assert "pull_request" not in events
    assert "pull_request_review" not in events
    assert "actions/checkout" not in workflow


def test_a_new_commit_cannot_merge_on_the_previous_one_s_checks() -> None:
    """What stands in for the removed review gate: required checks are read off the head SHA, so a
    push has none until CI reports on that commit. Both halves have to hold. `synchronize` is what
    starts the run, and `check` is the required context, so it has to aggregate the matrix rather
    than pass on its own while `Python 3.12` is still red."""
    ci = (WORKFLOWS / "ci.yml").read_text()
    events = workflow_events("ci.yml")

    assert "synchronize" in events["pull_request"]["types"]
    assert "needs: test" in ci
    assert "if: always()" in ci


def test_issue_hygiene_accepts_one_labeled_closing_issue() -> None:
    workflow = (WORKFLOWS / "issue-hygiene.yml").read_text()
    issues = [{"labels": {"totalCount": 1}}, {"labels": {"totalCount": 0}}]

    assert any(issue["labels"]["totalCount"] > 0 for issue in issues)
    assert "any(.labels.totalCount > 0)" in workflow


def test_dependabot_exemption_follows_pr_author() -> None:
    workflow = (WORKFLOWS / "issue-hygiene.yml").read_text()
    assert "github.event.pull_request.user.login != 'dependabot[bot]'" in workflow
    assert "github.actor != 'dependabot[bot]'" not in workflow


def test_issue_hygiene_skips_non_default_base_branch() -> None:
    """Closing references only exist for PRs based on the default branch, so a promotion PR
    (dev -> main) or a stacked PR (based on a feature branch) can never satisfy this check."""
    workflow = (WORKFLOWS / "issue-hygiene.yml").read_text()
    assert "github.event.pull_request.base.ref == github.event.repository.default_branch" in workflow


def test_release_promotion_checks_skip_on_main_base() -> None:
    """A dev -> main promotion PR's diff re-accumulates everything merged into dev since main's
    last advance; each change was already gated against dev as its base at merge time (#536)."""
    ci = (WORKFLOWS / "ci.yml").read_text()
    docs_impact = (WORKFLOWS / "docs-impact.yml").read_text()
    assert "github.base_ref != 'main'" in ci
    assert "github.event.pull_request.base.ref != 'main'" in docs_impact
