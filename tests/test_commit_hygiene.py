"""Commit metadata stays free of agent attribution and session links."""

from __future__ import annotations

from check_commit_hygiene import hygiene_findings


def test_normal_commit_is_clean() -> None:
    assert hygiene_findings("A Developer", "dev@example.com", "fix: preserve event ordering") == []


def test_attribution_trailer_is_rejected() -> None:
    message = "fix: preserve event ordering\n\nCo-Authored-By: Claude <noreply@anthropic.com>"
    assert "attribution trailer" in hygiene_findings("A Developer", "dev@example.com", message)


def test_generator_signature_is_rejected() -> None:
    message = "Generated with Claude Code"
    assert "generator signature" in hygiene_findings("A Developer", "dev@example.com", message)


def test_session_trailer_is_rejected() -> None:
    message = "Claude-Session: session_example"
    assert "Claude session metadata" in hygiene_findings("A Developer", "dev@example.com", message)


def test_bare_session_url_is_rejected() -> None:
    message = "https://claude.ai/code/session_example"
    assert "Claude session metadata" in hygiene_findings("A Developer", "dev@example.com", message)


def test_ai_author_identity_is_rejected() -> None:
    assert "AI author identity" in hygiene_findings("Claude", "noreply@anthropic.com", "fix: event ordering")


def test_human_vendor_employee_is_allowed() -> None:
    assert hygiene_findings("Alice Smith", "alice@anthropic.com", "fix: event ordering") == []
