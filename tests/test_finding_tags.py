"""Runs `finding_tags.py`'s own checks inside the suite.

The script is on no gate, so nothing else would notice it breaking, and its counting is what
`review-pr` Harvest and `deck-insight` both stand on: a miscount either invents a recurrence or
hides one (#697). The parsing is pure, so this needs no network.
"""

from __future__ import annotations

from finding_tags import _self_test


def test_only_tagged_blocks_and_discusses_count() -> None:
    _self_test()
