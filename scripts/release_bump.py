#!/usr/bin/env python3
"""Release bookkeeping: version bump, milestone/tracking issues, milestone close.

    scripts/release_bump.py bump X.Y.Z
    scripts/release_bump.py issues X.Y.Z
    scripts/release_bump.py promote X.Y.Z
    scripts/release_bump.py close-milestone X.Y.Z

Invoked a few times a year by the `release` skill; not a package feature.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REPO = "agentdecksdk/agentdeck"
PYPROJECT = REPO_ROOT / "pyproject.toml"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

Runner = Callable[[list[str]], str]

VERSION_RE = re.compile(r'(?m)^version = ".*"$')
UNRELEASED_RE = re.compile(r"(?ms)^## \[Unreleased\]\n\n(.*?)(?=^## \[)")
UNRELEASED_LINK_RE = re.compile(r"(?m)^\[Unreleased\]: (?P<prefix>\S+/compare/v)(?P<last>\S+?)\.\.\.HEAD$")
FIXES_RE = re.compile(r"(?im)^(?:fixes|closes)\s+#(\d+)")
TRAILING_PR_RE = re.compile(r"\(#(\d+)\)\s*$")


def run_command(args: list[str]) -> str:
    return subprocess.run(args, cwd=REPO_ROOT, check=True, capture_output=True, text=True).stdout


def bump_pyproject(text: str, version: str) -> str:
    new_text, count = VERSION_RE.subn(f'version = "{version}"', text, count=1)
    if count != 1:
        raise ValueError("pyproject.toml: no version line found")
    return new_text


def bump_changelog(text: str, version: str, date: str) -> str:
    """Move `[Unreleased]`'s entries under a new dated heading, then point the
    compare-link footer at it: the three manual edits done by hand for v5.2.0.
    """
    section = UNRELEASED_RE.search(text)
    if section is None:
        raise ValueError("CHANGELOG.md: no [Unreleased] section found")
    entries = section.group(1)
    if not entries.strip():
        raise ValueError("CHANGELOG.md: [Unreleased] section is empty, nothing to release")
    text = f"{text[: section.start()]}## [Unreleased]\n\n## [{version}] - {date}\n\n{entries}{text[section.end() :]}"

    link = UNRELEASED_LINK_RE.search(text)
    if link is None:
        raise ValueError("CHANGELOG.md: no [Unreleased] compare-link footer line found")
    prefix, last = link.group("prefix"), link.group("last")
    replacement = f"[Unreleased]: {prefix}{version}...HEAD\n[{version}]: {prefix}{last}...v{version}"
    return text[: link.start()] + replacement + text[link.end() :]


def apply_bump(version: str, date: str) -> None:
    PYPROJECT.write_text(bump_pyproject(PYPROJECT.read_text(), version), encoding="utf-8")
    CHANGELOG.write_text(bump_changelog(CHANGELOG.read_text(), version, date), encoding="utf-8")


def latest_tag(run: Runner) -> str:
    """The highest vX.Y.Z tag in the repo, not the closest one reachable from HEAD: a release
    tag lands on `main` (release.yml's promotion push), which `dev` never merges back in, so
    `git describe` from a `dev` checkout would answer with a stale ancestor tag instead.
    """
    tags = run(["git", "tag", "--list", "v*", "--sort=-v:refname"]).splitlines()
    if not tags:
        raise ValueError("no vX.Y.Z tags found in this repository")
    return tags[0]


def referenced_issues(text: str) -> list[int]:
    seen: dict[int, None] = {}
    for match in FIXES_RE.finditer(text):
        seen.setdefault(int(match.group(1)), None)
    return list(seen)


def commits_since(tag: str, run: Runner) -> list[tuple[str, str]]:
    """(hash, subject) pairs since ``tag``, oldest first."""
    pairs: list[tuple[str, str]] = []
    for line in run(["git", "log", "--reverse", f"{tag}..HEAD", "--format=%H%x09%s"]).splitlines():
        commit_hash, _, subject = line.partition("\t")
        pairs.append((commit_hash, subject))
    return pairs


def pr_closing_issues(pr: int, run: Runner) -> list[int]:
    """Issues ``pr`` closes, per GitHub's own tracking: correct regardless of merge strategy.

    Empty if ``pr`` doesn't resolve to a real PR: a squash subject's other ``(#N)`` is often
    the issue it closed, not a PR, and a stray reference should degrade, not abort the scan.
    """
    try:
        raw = run(["gh", "pr", "view", str(pr), "--repo", REPO, "--json", "closingIssuesReferences"])
    except subprocess.CalledProcessError:
        return []
    return [ref["number"] for ref in json.loads(raw)["closingIssuesReferences"]]


def referenced_issues_in_range(tag: str, run: Runner) -> list[int]:
    """Issues closed by commits since ``tag``: each commit's trailing ``(#N)`` names the
    squash-merged PR to ask GitHub directly; a commit without one (a direct push) falls back
    to scanning its own message for ``Fixes``/``Closes``.
    """
    seen: dict[int, None] = {}
    for commit_hash, subject in commits_since(tag, run):
        match = TRAILING_PR_RE.search(subject)
        if match:
            issues = pr_closing_issues(int(match.group(1)), run)
        else:
            issues = referenced_issues(run(["git", "log", "-1", "--format=%B", commit_hash]))
        for issue in issues:
            seen.setdefault(issue, None)
    return list(seen)


def milestone_number(title: str, run: Runner) -> int | None:
    milestones = json.loads(run(["gh", "api", f"repos/{REPO}/milestones", "--method", "GET", "-f", "state=all"]))
    return next((milestone["number"] for milestone in milestones if milestone["title"] == title), None)


def ensure_milestone(title: str, run: Runner) -> None:
    if milestone_number(title, run) is not None:
        return
    run(["gh", "api", f"repos/{REPO}/milestones", "-f", f"title={title}"])


def create_tracking_issue(title: str, body: str, milestone: str, run: Runner) -> int:
    url = run(
        [
            "gh",
            "issue",
            "create",
            "--repo",
            REPO,
            "--title",
            title,
            "--body",
            body,
            "--label",
            "chore",
            "--milestone",
            milestone,
        ]
    ).strip()
    return int(url.rsplit("/", 1)[-1])


def cmd_issues(version: str, run: Runner = run_command) -> int:
    milestone = f"v{version}"
    ensure_milestone(milestone, run)

    issues = referenced_issues_in_range(latest_tag(run), run)
    for issue in issues:
        run(["gh", "issue", "edit", str(issue), "--repo", REPO, "--milestone", milestone])

    work = ", ".join(f"#{issue}" for issue in issues) if issues else "none found in the commit range"
    body = (
        f"Tracking issue for the {milestone} version bump / CHANGELOG cut, closed by the release PR. "
        f"No code change of its own; the milestone's real work is {work} (see milestone {milestone})."
    )
    number = create_tracking_issue(f"chore: cut and ship {milestone}", body, milestone, run)
    print(number)
    return number


def cmd_promote(version: str, run: Runner = run_command) -> int:
    milestone = f"v{version}"
    ensure_milestone(milestone, run)
    body = (
        f"Tracking issue for the dev->main promotion merge for {milestone}. Must be closed by hand: main is not "
        f"the default branch, so a closing keyword on a main-based PR never registers. See milestone {milestone} "
        "for the release's tracking issues."
    )
    number = create_tracking_issue(f"chore: release {milestone} to main", body, milestone, run)
    print(number)
    return number


def cmd_close_milestone(version: str, run: Runner = run_command) -> None:
    title = f"v{version}"
    number = milestone_number(title, run)
    if number is None:
        raise SystemExit(f"no milestone named {title!r} in {REPO}")
    run(["gh", "api", f"repos/{REPO}/milestones/{number}", "--method", "PATCH", "-f", "state=closed"])


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subcommands = parser.add_subparsers(dest="command", required=True)
    for name in ("bump", "issues", "promote", "close-milestone"):
        sub = subcommands.add_parser(name)
        sub.add_argument("version", help="release version, e.g. 5.3.0")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "bump":
        apply_bump(args.version, datetime.date.today().isoformat())
    elif args.command == "issues":
        cmd_issues(args.version)
    elif args.command == "promote":
        cmd_promote(args.version)
    elif args.command == "close-milestone":
        cmd_close_milestone(args.version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
