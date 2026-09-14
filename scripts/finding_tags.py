#!/usr/bin/env python3
"""Count dev-agent behavior tags across this repository's review comments.

`review-pr` Harvest asks whether a behavior has appeared more than once. Across sessions the
reviewer has no memory, so that bar was reachable only by luck (#697). Every finding is already a
review comment on GitHub carrying its class and its `tag:` line, so this counts them there rather
than in a file the reviewer would have to commit.

Not a gate: nothing here fails a build. Slop rules caught 0 of 14 findings in v6, and another
mechanical guard is what #697 forbids.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

REPO = "agentdecksdk/agentdeck"
REFINEMENT_THRESHOLD = 3
# A finding opens with its class in bold (`templates.md` Template A) and tags itself on its own
# line. Both anchors are required: prose quoting a tag is not a finding.
CLASS = re.compile(r"^\*\*(BLOCK|DISCUSS)\*\*", re.MULTILINE)
TAG = re.compile(r"^tag:\s*([a-z0-9][a-z0-9-]*)\s*$", re.MULTILINE)


def tags(comments: Iterable[str]) -> Counter[str]:
    found: Counter[str] = Counter()
    for body in comments:
        if CLASS.search(body) and (tag := TAG.search(body)):
            found[tag.group(1)] += 1
    return found


def fetch() -> list[str]:
    out = subprocess.run(
        ["gh", "api", f"repos/{REPO}/pulls/comments", "--paginate", "--jq", ".[].body"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return out.splitlines()


def report(found: Counter[str]) -> str:
    if not found:
        return "findings: no tagged review comments yet."
    lines = [f"findings: {sum(found.values())} tagged, {len(found)} tags"]
    lines += [f"  {count:>3}  {tag}" for tag, count in found.most_common()]
    over = [tag for tag, count in found.most_common() if count >= REFINEMENT_THRESHOLD]
    lines.append(
        f"at or over {REFINEMENT_THRESHOLD}: {', '.join(over)}"
        if over
        else f"at or over {REFINEMENT_THRESHOLD}: none. Nothing to refine."
    )
    return "\n".join(lines)


def _self_test() -> None:
    assert tags([]) == Counter()
    assert "no tagged review comments" in report(tags([]))

    six = ["**BLOCK** The assertion cannot fail.\n\ntag: assertion-cannot-fail"] * 6
    assert tags(six)["assertion-cannot-fail"] == 6
    assert "at or over 3: assertion-cannot-fail" in report(tags(six))

    mixed = [
        "**BLOCK** a.\n\ntag: tag-a",
        "**DISCUSS** b.\n\nSettled by: a benchmark.\n\ntag: tag-a",
        "**BLOCK** c.\n\ntag: tag-b",
    ]
    assert tags(mixed) == Counter({"tag-a": 2, "tag-b": 1})
    assert "at or over 3: none" in report(tags(mixed)), "two findings is not a refinement"

    assert tags(["**NIT** rename this.\n\ntag: naming"]) == Counter(), "a NIT is not counted"
    assert tags(["Reuse an existing tag: assertion-cannot-fail is the one that fits"]) == Counter(), (
        "prose quoting a tag is not a finding"
    )
    assert tags(["**BLOCK** no tag line here"]) == Counter(), "an untagged finding cannot recur"


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        _self_test()
        print("finding_tags: self-test ok")
    else:
        print(report(tags(fetch())))
