#!/usr/bin/env python3
"""Structural cost of a branch vs its merge base, so repository entropy is
visible per PR instead of discovered months later.

Usage: uv run scripts/quality_delta.py [base-ref]   (default: origin/dev)
"""

from __future__ import annotations

import re
import subprocess
import sys

SKIP = ("uv.lock", ".png", ".jpg", ".svg", ".lock")
PUBLIC_DEF_RE = re.compile(r"^\+\s*(?:async )?(?:def|class) [A-Za-z]")
COMMENT_RE = re.compile(r"^\+\s*#(?!\s*(!|type:|noqa|ruff:|fmt:|pragma|ponytail:))")
TODO_RE = re.compile(r"^\+.*\b(TODO|FIXME|HACK)\b", re.IGNORECASE)
DEP_FILE_RE = re.compile(r"^\+\+\+ b/pyproject\.toml")
DEP_LINE_RE = re.compile(r'^\+\s*"[A-Za-z0-9_.-]+[>=<!~]')


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, timeout=30).stdout


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "origin/dev"
    merge_base = _git("merge-base", base, "HEAD").strip()
    if not merge_base:
        print(f"no merge base with {base}; pass an explicit ref", file=sys.stderr)
        return 1

    code = {"+": 0, "-": 0}
    other = {"+": 0, "-": 0}
    new_files = 0
    for line in _git("diff", "--numstat", merge_base, "HEAD").splitlines():
        plus, minus, path = line.split("\t", 2)
        if path.endswith(SKIP) or plus == "-":
            continue
        bucket = code if path.endswith(".py") else other
        bucket["+"] += int(plus)
        bucket["-"] += int(minus)
    new_files = sum(
        1
        for line in _git("diff", "--name-status", merge_base, "HEAD").splitlines()
        if line.startswith("A") and not line.rstrip().endswith(SKIP)
    )

    new_defs, comments, todos, new_deps = [], 0, 0, 0
    in_py = in_pyproject = False
    for line in _git("diff", merge_base, "HEAD").splitlines():
        if line.startswith("+++ "):
            in_pyproject = bool(DEP_FILE_RE.match(line))
            in_py = line.endswith(".py")
            continue
        if in_py:
            if PUBLIC_DEF_RE.match(line):
                new_defs.append(line.lstrip("+").strip().split("(")[0])
            if COMMENT_RE.match(line):
                comments += 1
            if TODO_RE.match(line):
                todos += 1
        if in_pyproject and DEP_LINE_RE.match(line):
            new_deps += 1

    print(f"Quality delta vs {base} ({merge_base[:9]})")
    print(f"  code LOC (.py): +{code['+']} -{code['-']} (net {code['+'] - code['-']:+d})")
    print(f"  other LOC: +{other['+']} -{other['-']}, new files: {new_files}")
    print(f"  new public defs/classes: {len(new_defs)}")
    for name in new_defs[:20]:
        print(f"    {name}")
    print(f"  comment lines added: {comments}")
    print(f"  TODO/FIXME/HACK added: {todos}")
    print(f"  dependency lines added: {new_deps}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
