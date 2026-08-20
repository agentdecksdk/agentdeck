#!/usr/bin/env python3
"""Reject attribution and private-session metadata in new commits."""

from __future__ import annotations

import re
import subprocess
import sys

PROHIBITED_MESSAGE = {
    "attribution trailer": re.compile(r"^Co-Authored-By:", re.IGNORECASE | re.MULTILINE),
    "generator signature": re.compile(r"^\W*Generated with\b", re.IGNORECASE | re.MULTILINE),
    "Claude session metadata": re.compile(r"^Claude-Session:|https://claude\.ai/code/", re.IGNORECASE | re.MULTILINE),
}
AI_AUTHOR_NAME = re.compile(r"^(?:claude(?: (?:code|opus(?: \d+)?|sonnet(?: \d+)?))?|codex)$", re.IGNORECASE)
AI_AUTHOR_EMAIL = re.compile(r"^(?:noreply|claude)@anthropic\.com$|^(?:noreply|codex)@openai\.com$", re.IGNORECASE)


def hygiene_findings(author_name: str, author_email: str, message: str) -> list[str]:
    findings = [label for label, pattern in PROHIBITED_MESSAGE.items() if pattern.search(message)]
    if AI_AUTHOR_NAME.match(author_name) and AI_AUTHOR_EMAIL.match(author_email):
        findings.append("AI author identity")
    return findings


def commits_since(base: str) -> list[tuple[str, str, str, str]]:
    output = subprocess.check_output(
        ["git", "log", "--format=%H%x00%an%x00%ae%x00%B%x00", f"{base}..HEAD"],
        text=True,
    )
    fields = output.split("\x00")
    if fields and not fields[-1].strip():
        fields.pop()
    return [
        (fields[index], fields[index + 1], fields[index + 2], fields[index + 3]) for index in range(0, len(fields), 4)
    ]


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("usage: check_commit_hygiene.py <base-ref>", file=sys.stderr)
        return 2

    failed = False
    for commit, author_name, author_email, message in commits_since(args[0]):
        findings = hygiene_findings(author_name, author_email, message)
        if findings:
            failed = True
            print(f"{commit[:12]}: {', '.join(findings)}", file=sys.stderr)
    if failed:
        print("remove attribution and session metadata from new commits", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
