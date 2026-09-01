#!/usr/bin/env python3
"""A PR's changelog entry belongs under `[Unreleased]`, never inside a released section.

Usage: changelog_position.py <base-ref>

`release_bump.py` inserts the new version heading *above* the entries it releases, so a branch
cut before a release still adds its entry at the same context and git re-applies it there,
under the version that just shipped (#545 and #546 landed in `[5.2.0]` that way).
"""

from __future__ import annotations

import re
import subprocess
import sys

CHANGELOG = "CHANGELOG.md"
HUNK_RE = re.compile(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@", re.MULTILINE)
RELEASED_HEADING_RE = re.compile(r"^## \[\d[^]]*]", re.MULTILINE)


def added_line_numbers(diff: str) -> list[int]:
    """The new-file line numbers this diff adds, from a `--unified=0` diff of one file."""
    added: list[int] = []
    line_number = 0
    for line in diff.splitlines():
        if hunk := HUNK_RE.match(line):
            line_number = int(hunk.group(1))
            continue
        if line.startswith("+") and not line.startswith("+++"):
            added.append(line_number)
            line_number += 1
    return added


def first_released_line(text: str) -> int | None:
    """The 1-based line of the first released heading, or None if the file holds no release yet."""
    match = RELEASED_HEADING_RE.search(text)
    return None if match is None else text[: match.start()].count("\n") + 1


def misplaced(diff: str, text: str) -> list[int]:
    """Added lines that landed at or below the first released heading.

    A release bump is the one exempt case, and it is exempt because the boundary itself is one of
    its added lines: `release_bump.py` inserts the new heading above the entries it releases. A PR
    that adds some other version heading lower down is not exempt, so it cannot smuggle an entry
    into an older section.
    """
    boundary = first_released_line(text)
    if boundary is None:
        return []
    added = added_line_numbers(diff)
    if boundary in added:
        return []
    return [number for number in added if number >= boundary]


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("usage: changelog_position.py <base-ref>", file=sys.stderr)
        return 2
    # The merge base, not the base ref: dev advancing after the branch was cut must not turn
    # someone else's merged entry into this PR's finding.
    base = subprocess.check_output(["git", "merge-base", args[0], "HEAD"], text=True).strip()
    diff = subprocess.check_output(["git", "diff", "--unified=0", base, "HEAD", "--", CHANGELOG], text=True)
    if not diff.strip():
        return 0
    with open(CHANGELOG, encoding="utf-8") as handle:
        text = handle.read()
    if lines := misplaced(diff, text):
        print(
            f"{CHANGELOG}: lines {lines} were added inside a released section. Move the entry under "
            "`## [Unreleased]`: a released heading describes what already shipped.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
