"""Structural checks for release-note navigation."""

from __future__ import annotations

import re
from pathlib import Path

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
