#!/usr/bin/env python3
"""Count behavior tags in `docs/delivery/finding-ledger.md`.

`review-pr` Harvest asks whether a dev-agent behavior has appeared more than once. Before the
ledger that was a question about the reviewer's memory across sessions, which is no memory at all
(#697). This reads the rows and answers it arithmetically, so `deck-insight` spends its judgment on
what a repeat means rather than on finding one.

Not a gate: nothing here fails a build. Slop rules caught 0 of 14 findings in v6, and another
mechanical guard is what #697 forbids.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

LEDGER = Path(__file__).resolve().parent.parent / "docs/delivery/finding-ledger.md"
REFINEMENT_THRESHOLD = 3


def tags(ledger: str) -> Counter[str]:
    """Tags from data rows only: a row is four pipe-delimited cells whose class cell is a class."""
    found: Counter[str] = Counter()
    for line in ledger.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 4 and cells[2] in {"BLOCK", "DISCUSS"}:
            found[cells[3]] += 1
    return found


def report(ledger: str) -> str:
    found = tags(ledger)
    if not found:
        return "finding ledger: no rows yet."
    lines = [f"finding ledger: {sum(found.values())} rows, {len(found)} tags"]
    lines += [f"  {count:>3}  {tag}" for tag, count in found.most_common()]
    over = [tag for tag, count in found.most_common() if count >= REFINEMENT_THRESHOLD]
    lines.append(
        f"at or over {REFINEMENT_THRESHOLD}: {', '.join(over)}"
        if over
        else f"at or over {REFINEMENT_THRESHOLD}: none. Nothing to refine."
    )
    return "\n".join(lines)


def _self_test() -> None:
    empty = "| PR | site | class | behavior tag |\n|---|---|---|---|\n"
    assert tags(empty) == Counter(), "the header row is not a finding"
    assert "no rows yet" in report(empty)

    six_v6_blocks = empty + "\n".join(
        f"| {pr} | somewhere.py:1 | BLOCK | assertion-cannot-fail |" for pr in (607, 577, 575, 566, 617, 625)
    )
    assert tags(six_v6_blocks)["assertion-cannot-fail"] == 6
    assert "at or over 3: assertion-cannot-fail" in report(six_v6_blocks)

    under = empty + (
        "| 1 | a.py:1 | BLOCK | tag-a |\n| 2 | b.py:1 | DISCUSS | tag-a |\n| 3 | c.py:1 | BLOCK | tag-b |\n"
    )
    counted = tags(under)
    assert counted == Counter({"tag-a": 2, "tag-b": 1}), counted
    assert "at or over 3: none" in report(under), "two rows is not a refinement"

    prose = empty + "Reuse an existing tag when one fits | it is not a row |\n"
    assert tags(prose) == Counter(), "prose with pipes is not a row"


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        _self_test()
        print("finding_ledger: self-test ok")
    else:
        print(report(LEDGER.read_text(encoding="utf-8")))
