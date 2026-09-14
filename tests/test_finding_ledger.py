"""Runs `finding_ledger.py`'s own checks inside the suite.

The script is not on any gate, so nothing else would notice it breaking. Its counting is what
`review-pr` Harvest and `deck-insight` both stand on: a miscount either invents a recurrence or
hides one (#697).
"""

from __future__ import annotations

from finding_ledger import _self_test


def test_the_ledger_counts_tags_and_not_prose() -> None:
    _self_test()
