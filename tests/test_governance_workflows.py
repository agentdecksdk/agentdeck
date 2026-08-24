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
    for name in ("agent-review.yml", "issue-hygiene.yml"):
        events = workflow_events(name)
        workflow = (WORKFLOWS / name).read_text()
        assert "pull_request_target" in events
        assert "pull_request" not in events
        assert "pull_request_review" not in events
        assert "actions/checkout" not in workflow


def test_the_review_rerun_poker_is_not_a_gate_and_cannot_be_driven_by_a_fork() -> None:
    """`pull_request_review` is a trusted base-branch event, but its ref is the merge commit, so a
    check run it published would miss the head SHA branch protection reads. This workflow therefore
    reruns the real gate rather than being one, and only a member's review may spend `actions: write`."""
    workflow = (WORKFLOWS / "agent-review-rerun.yml").read_text()
    events = workflow_events("agent-review-rerun.yml")

    assert list(events) == ["pull_request_review"]
    assert "actions/checkout" not in workflow
    assert "github.event.review.author_association == 'MEMBER'" in workflow
    assert "gh run rerun" in workflow


def test_issue_hygiene_accepts_one_labeled_closing_issue() -> None:
    workflow = (WORKFLOWS / "issue-hygiene.yml").read_text()
    issues = [{"labels": {"totalCount": 1}}, {"labels": {"totalCount": 0}}]

    assert any(issue["labels"]["totalCount"] > 0 for issue in issues)
    assert "any(.labels.totalCount > 0)" in workflow


def test_dependabot_exemption_follows_pr_author() -> None:
    workflow = (WORKFLOWS / "issue-hygiene.yml").read_text()
    assert "github.event.pull_request.user.login != 'dependabot[bot]'" in workflow
    assert "github.actor != 'dependabot[bot]'" not in workflow
