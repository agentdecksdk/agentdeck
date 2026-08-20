"""Require documentation pages affected by source changes to be reviewed."""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTENT_ROOT = REPO_ROOT / "docs-site" / "content"
DOCS_SOURCES = re.compile(r"^\{/\* docs_sources:\n(?P<sources>(?:  - .+\n)+)\*/\}", re.MULTILINE)


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


def changed_files(base: str, head: str) -> tuple[str, ...]:
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACDMR", base, head],
        cwd=REPO_ROOT,
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/dev", help="base git revision")
    parser.add_argument("--head", default="HEAD", help="head git revision")
    parser.add_argument(
        "--acknowledge-review",
        action="store_true",
        help="report impacted pages without failing after unchanged pages were reviewed",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        mappings = load_mappings()
        changes = changed_files(args.base, args.head)
    except (OSError, KeyError, TypeError, ValueError, subprocess.CalledProcessError) as error:
        print(f"docs impact configuration error: {error}", file=sys.stderr)
        return 2

    impacted = impacted_pages(mappings, changes)
    missing = check_docs_impact(mappings, changes)
    if not impacted:
        print("No mapped documentation pages are affected.")
        return 0

    print("Documentation pages affected by this change:")
    for mapping in impacted:
        state = "updated" if mapping.path in changes else "review needed"
        print(f"  {state}: {mapping.path}")
        matches = sorted(
            change for change in changes if any(fnmatch.fnmatchcase(change, pattern) for pattern in mapping.sources)
        )
        for match in matches:
            print(f"    source: {match}")

    if not missing or args.acknowledge_review:
        return 0

    print(
        'Update each affected page, or check "Unchanged pages in the docs impact report were reviewed" in the PR.',
        file=sys.stderr,
    )
    for mapping in missing:
        print(f"::error file={mapping.path}::Review this page for the source changes in this PR", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
