#!/usr/bin/env python3
"""Concepts are expensive: a PR may not introduce more classes, modules, public
symbols, or dependencies than its own '## Concept budget' declares.

Usage: PR_BODY="$(...)" uv run scripts/concept_budget.py [base-ref]
Passes silently when the PR introduces no concepts at all; otherwise the budget
section and, for public symbols, a '## Reuse analysis' section are required.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

DEP_LINE_RE = re.compile(r'^\+\s*"[A-Za-z0-9_.-]+[>=<!~]')
TOP_LEVEL_RE = re.compile(r"^\+(async def|def|class) [A-Za-z]")
BUDGET_RE = re.compile(r"new (classes|public symbols|modules|dependencies)\s*:\s*(\d+)", re.IGNORECASE)


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, timeout=30).stdout


def actual_concepts(merge_base: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {"classes": [], "public symbols": [], "modules": [], "dependencies": []}
    for line in _git("diff", "--name-status", merge_base, "HEAD").splitlines():
        status, _, path = line.partition("\t")
        if status == "A" and path.startswith("agentdeck/") and path.endswith(".py"):
            found["modules"].append(path)
    in_lib = in_pyproject = False
    for line in _git("diff", merge_base, "HEAD").splitlines():
        if line.startswith("+++ "):
            in_lib = line.startswith("+++ b/agentdeck/") and line.endswith(".py")
            in_pyproject = line == "+++ b/pyproject.toml"
            continue
        if in_lib and (match := TOP_LEVEL_RE.match(line)):
            name = line.lstrip("+").split("(")[0].split(":")[0].strip()
            found["public symbols"].append(name)
            if match.group(1) == "class":
                found["classes"].append(name)
        if in_pyproject and DEP_LINE_RE.match(line):
            found["dependencies"].append(line.lstrip("+ ").strip())
    return found


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "origin/dev"
    merge_base = _git("merge-base", base, "HEAD").strip()
    if not merge_base:
        print(f"no merge base with {base}", file=sys.stderr)
        return 1
    body = os.environ.get("PR_BODY", "")
    declared = {k.lower(): int(v) for k, v in BUDGET_RE.findall(body)}
    actual = actual_concepts(merge_base)

    print(f"Concept budget vs {base} ({merge_base[:9]})")
    for kind, items in actual.items():
        print(f"  new {kind}: {len(items)} (declared: {declared.get(kind, 'none')})")
        for item in items[:10]:
            print(f"    {item}")

    if not any(actual.values()):
        return 0
    failures = []
    if not declared:
        failures.append("PR introduces concepts but has no '## Concept budget' section")
    else:
        failures.extend(
            f"new {kind}: {len(items)} exceeds declared {declared.get(kind, 0)}"
            for kind, items in actual.items()
            if len(items) > declared.get(kind, 0)
        )
    if actual["public symbols"] and "## Reuse analysis" not in body:
        failures.append("new public symbols require a '## Reuse analysis' section")
    for failure in failures:
        print(f"::error::{failure} (CLAUDE.md concept budget: run 'uv run scripts/repomap.py', reuse before creation)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
