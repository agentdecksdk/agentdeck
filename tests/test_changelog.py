"""Structural checks for release-note navigation."""

from __future__ import annotations

import re
from pathlib import Path

from changelog_position import misplaced

CHANGELOG = Path(__file__).parents[1] / "CHANGELOG.md"
COMPARE_ROOT = "https://github.com/agentdecksdk/agentdeck/compare"


def test_every_release_has_an_adjacent_compare_link() -> None:
    text = CHANGELOG.read_text()
    versions = re.findall(r"^## \[([^]]+)](?: - .+)?$", text, re.MULTILINE)
    links = dict(re.findall(r"^\[([^]]+)]: (\S+)$", text, re.MULTILINE))

    assert versions[0] == "Unreleased"
    assert links["Unreleased"] == f"{COMPARE_ROOT}/v{versions[1]}...HEAD"
    for current, previous in zip(versions[1:-1], versions[2:], strict=True):
        assert links[current] == f"{COMPARE_ROOT}/v{previous}...v{current}"

    oldest = versions[-1]
    assert links[oldest] == f"https://github.com/agentdecksdk/agentdeck/releases/tag/v{oldest}"


UNRELEASED_TEXT = "# Changelog\n\n## [Unreleased]\n\n- new entry.\n\n## [1.0.0] - 2026-01-01\n\n- shipped.\n"
RELEASED_TEXT = "# Changelog\n\n## [Unreleased]\n\n## [1.0.0] - 2026-01-01\n\n- new entry.\n- shipped.\n"
BUMPED_TEXT = (
    "# Changelog\n\n## [Unreleased]\n\n## [1.1.0] - 2026-02-01\n\n- new entry.\n\n"
    "## [1.0.0] - 2026-01-01\n\n- shipped.\n"
)


def test_an_entry_under_unreleased_passes_and_one_inside_a_release_fails() -> None:
    assert misplaced("@@ -3,0 +5 @@\n+- new entry.\n", UNRELEASED_TEXT) == []
    assert misplaced("@@ -5,0 +7 @@\n+- new entry.\n", RELEASED_TEXT) == [7]


def test_a_release_bump_is_exempt_because_it_adds_the_boundary_itself() -> None:
    bump = "@@ -3,0 +5,3 @@\n+## [1.1.0] - 2026-02-01\n+\n+- new entry.\n"
    assert misplaced(bump, BUMPED_TEXT) == []


def test_a_new_heading_lower_down_does_not_exempt_an_entry_in_an_older_section() -> None:
    """The exemption is the boundary being new, not any version heading appearing in the diff:
    otherwise one added heading would wave through an entry smuggled into a shipped release."""
    smuggled = "@@ -5,0 +7 @@\n+## [1.1.0] - 2026-02-01\n@@ -8,0 +11 @@\n+- smuggled.\n"
    assert misplaced(smuggled, RELEASED_TEXT) == [7, 11]
