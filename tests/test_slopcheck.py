"""Runs `slopcheck.py`'s own regression harness inside the suite. `_self_test()` holds every
SLOP001-013 assertion, including the `check_file`/`--write` wiring, but `make check`'s `slop`
target only lints this branch's changed lines and never calls it, so a broken rule or a broken
wiring passed the gate green (#531). `_self_test()` builds its own throwaway git repo under
`tempfile.TemporaryDirectory()` to exercise `check_file` and the `--write` hook, so this test
touches nothing in the real worktree.
"""

from __future__ import annotations

from slopcheck import _self_test


def test_every_slop_rule_still_flags_what_it_is_for() -> None:
    _self_test()
