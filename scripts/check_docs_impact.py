"""Require documentation pages affected by source changes to be reviewed."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTENT_PREFIX = "docs-site/content/"
CONTENT_ROOT = REPO_ROOT / "docs-site" / "content"
DOCS_SOURCES = re.compile(r"^\{/\* docs_sources:\n(?P<sources>(?:  - .+\n)+)\*/\}", re.MULTILINE)
ACKNOWLEDGEMENT = re.compile(r"^\s*- \[[xX]\] Unchanged pages reviewed: *(?P<pages>.+)$", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class PageMapping:
    path: str
    sources: tuple[str, ...]


def _sources_from_mdx(path: Path) -> tuple[str, ...]:
    page_name = path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path
    matches = list(DOCS_SOURCES.finditer(path.read_text()))
    if len(matches) != 1:
        raise ValueError(f"{page_name} must have exactly one docs_sources block")

    sources = []
    for line in matches[0].group("sources").splitlines():
        source = json.loads(line.removeprefix("  - "))
        if not isinstance(source, str):
            raise ValueError(f"{page_name} has a non-string docs_sources entry")
        sources.append(source)
    return tuple(sources)


def load_mappings(content_root: Path = CONTENT_ROOT) -> tuple[PageMapping, ...]:
    mappings = tuple(
        PageMapping(path.relative_to(REPO_ROOT).as_posix(), _sources_from_mdx(path))
        for path in sorted(content_root.rglob("*.mdx"))
    )
    validate_mappings(mappings)
    return mappings


def validate_mappings(mappings: tuple[PageMapping, ...]) -> None:
    mapped_pages = [mapping.path for mapping in mappings]
    duplicates = sorted({path for path in mapped_pages if mapped_pages.count(path) > 1})
    if duplicates:
        raise ValueError(f"duplicate documentation mappings: {', '.join(duplicates)}")

    empty = sorted(mapping.path for mapping in mappings if not mapping.sources)
    if empty:
        raise ValueError(f"pages without source patterns: {', '.join(empty)}")

    files_result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    repository_files = tuple(files_result.stdout.splitlines())
    unmatched = sorted(
        f"{mapping.path}: {pattern}"
        for mapping in mappings
        for pattern in mapping.sources
        if not any(fnmatch.fnmatchcase(path, pattern) for path in repository_files)
    )
    if unmatched:
        raise ValueError(f"source patterns matching no files: {', '.join(unmatched)}")


def page_key(path: str) -> str:
    """A page named the short way, so an acknowledgement may spell it either way."""
    return path.removeprefix(CONTENT_PREFIX)


def acknowledged_pages(body: str) -> frozenset[str]:
    """Pages a PR body claims were read and found still correct.

    Named rather than ticked, so the claim expires when a new page becomes impacted and not
    merely because another commit was pushed: a box re-ticked every push gets ticked unread.
    """
    # GitHub serves bodies with CRLF, so the last name carries a trailing \r out of the match.
    if (match := ACKNOWLEDGEMENT.search(body)) is None:
        return frozenset()
    return frozenset(page_key(page.strip()) for page in match.group("pages").split(",") if page.strip())


def changed_files(base: str, head: str, repo_root: Path = REPO_ROOT) -> tuple[str, ...]:
    result = subprocess.run(
        ["git", "diff", "--no-renames", "--name-only", "--diff-filter=ACDMR", f"{base}...{head}"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return tuple(line for line in result.stdout.splitlines() if line)


def impacted_pages(
    mappings: tuple[PageMapping, ...],
    changes: tuple[str, ...],
) -> tuple[PageMapping, ...]:
    return tuple(
        mapping
        for mapping in mappings
        if any(fnmatch.fnmatchcase(change, pattern) for change in changes for pattern in mapping.sources)
    )


def check_docs_impact(
    mappings: tuple[PageMapping, ...],
    changes: tuple[str, ...],
) -> tuple[PageMapping, ...]:
    changed = set(changes)
    return tuple(mapping for mapping in impacted_pages(mappings, changes) if mapping.path not in changed)


def _report(impacted: tuple[PageMapping, ...], missing: tuple[PageMapping, ...]) -> int:
    """The `make check` view: which pages to open, and the one line that clears them.

    Terse and last in the gate, because an advisory buried under pytest output is one nobody
    reads, and a report that only describes leaves the reader still deciding what to do.
    """
    if not missing:
        print(f"docs impact: {len(impacted)} affected pages, all updated or already reviewed.")
        return 0

    print(f"\ndocs impact: {len(missing)} of {len(impacted)} affected pages are neither updated nor reviewed.")
    for mapping in missing:
        print(f"  {mapping.path}")
    print("Open each. Fix what this change made wrong here, then paste this into the PR body:")
    print(f"  - [x] Unchanged pages reviewed: {', '.join(page_key(mapping.path) for mapping in missing)}\n")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/dev", help="base git revision")
    parser.add_argument("--head", default="HEAD", help="head git revision")
    parser.add_argument(
        "--report",
        action="store_true",
        help="list the affected pages without failing on the ones left unchanged",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        mappings = load_mappings()
    except (OSError, KeyError, TypeError, ValueError) as error:
        print(f"docs impact configuration error: {error}", file=sys.stderr)
        return 2

    try:
        changes = changed_files(args.base, args.head)
    except subprocess.CalledProcessError as error:
        if not args.report:
            print(f"docs impact cannot diff {args.base}...{args.head}: {error}", file=sys.stderr)
            return 2
        print(f"No {args.base} to diff against, so no impact report. Run `git fetch origin`.")
        return 0

    impacted = impacted_pages(mappings, changes)
    if not impacted:
        print("No mapped documentation pages are affected.")
        return 0

    acknowledged = acknowledged_pages(os.environ.get("PR_BODY", ""))
    missing = tuple(
        mapping for mapping in check_docs_impact(mappings, changes) if page_key(mapping.path) not in acknowledged
    )
    if missing and "PR_BODY" not in os.environ:
        print("PR_BODY unset: pages below may already be acknowledged in the PR body.", file=sys.stderr)

    if args.report:
        return _report(impacted, missing)

    print("Documentation pages affected by this change:")
    for mapping in impacted:
        if mapping.path in changes:
            state = "updated"
        elif mapping in missing:
            state = "review needed"
        else:
            state = "reviewed"
        print(f"  {state}: {mapping.path}")
        matches = sorted(
            change for change in changes if any(fnmatch.fnmatchcase(change, pattern) for pattern in mapping.sources)
        )
        for match in matches:
            print(f"    source: {match}")

    if not missing or args.report:
        return 0

    sys.stdout.flush()
    reviewed = ", ".join(page_key(mapping.path) for mapping in missing)
    print(
        f"Update each affected page, or name it in the PR body: `- [x] Unchanged pages reviewed: {reviewed}`",
        file=sys.stderr,
    )
    print(
        "A `gh run rerun` reuses the PR body from the event that triggered it; edit the body, "
        "then push or re-edit the PR to get a fresh check.",
        file=sys.stderr,
    )
    for mapping in missing:
        print(f"::error file={mapping.path}::Review this page for the source changes in this PR", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
