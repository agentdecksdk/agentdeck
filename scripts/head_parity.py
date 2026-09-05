"""Diffs the built `<head>` of two docs-site build trees, page by page.

Verifying a `docs-site/` change means proving the built `<head>` did not regress, and that check
was rebuilt from scratch in a scratchpad three times in two days (#689, #691). Twice it caught a
real regression, and both times only after the net was widened past `<title>` to the whole head:
`description` silently dropping for pages with no frontmatter description, and `theme-color`
(emitted by the old theme, replaced by nothing) surviving a regex over `description|og:|...`.
So this compares, per page: `<title>`, every `<meta name|property>`, every
`<link rel=canonical|alternate>`, and every `<script type="application/ld+json">` block (parsed,
key-sorted so formatting cannot mask a value change).

    python scripts/head_parity.py <build-a> <build-b>

Each argument is a build's static-export root (`docs-site/out/` from `npm run build`). Not a CI
gate and not a `make check` target: it costs two full builds, and what it protects against is a
framework or layout change, not an ordinary content edit. Run it by hand when reviewing one.
"""

from __future__ import annotations

import argparse
import json
import sys
from html.parser import HTMLParser
from pathlib import Path

HeadFields = tuple[str | None, tuple[str, ...], tuple[str, ...], tuple[str, ...]]

FIELDS_COMPARED = (
    "<title>; every <meta name|property>; every <link rel=canonical|alternate>; "
    "every JSON-LD <script> block (parsed, key-sorted)"
)


class HeadParser(HTMLParser):
    """Collects the fields `head_parity` compares from one page's `<head>`.

    A `<meta>`/`<link>` tag is rendered as its sorted `attr="value"` attributes, not just the
    field the spec names it for (`content`, `href`), so a change to any other attribute
    (`media`, `hreflang`) is not silently invisible.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_head = False
        self._in_title = False
        self._ldjson_depth = 0
        self.title: str | None = None
        self.metas: list[str] = []
        self.links: list[str] = []
        self.ldjson: list[str] = []
        self._title_buf: list[str] = []
        self._ldjson_buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "head":
            self._in_head = True
            return
        if not self._in_head:
            return
        attrs_dict = dict(attrs)
        if tag == "title":
            self._in_title = True
            self._title_buf = []
        elif tag == "meta" and ("name" in attrs_dict or "property" in attrs_dict):
            self.metas.append(_render_attrs(attrs))
        elif tag == "link" and attrs_dict.get("rel") in ("canonical", "alternate"):
            self.links.append(_render_attrs(attrs))
        elif tag == "script" and attrs_dict.get("type") == "application/ld+json":
            self._ldjson_depth += 1
            self._ldjson_buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "head":
            self._in_head = False
        elif tag == "title" and self._in_title:
            self._in_title = False
            self.title = "".join(self._title_buf)
        elif tag == "script" and self._ldjson_depth:
            self._ldjson_depth = 0
            self.ldjson.append(_canonical_json("".join(self._ldjson_buf)))

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_buf.append(data)
        elif self._ldjson_depth:
            self._ldjson_buf.append(data)


def _render_attrs(attrs: list[tuple[str, str | None]]) -> str:
    return " ".join(f'{name}="{value}"' for name, value in sorted(attrs))


def _canonical_json(raw: str) -> str:
    try:
        return json.dumps(json.loads(raw), sort_keys=True)
    except json.JSONDecodeError:
        return raw.strip()


def extract_head(html: str) -> HeadFields:
    parser = HeadParser()
    parser.feed(html)
    parser.close()
    return parser.title, tuple(sorted(parser.metas)), tuple(sorted(parser.links)), tuple(sorted(parser.ldjson))


def _discover_pages(build_dir: Path) -> dict[str, Path]:
    return {path.relative_to(build_dir).as_posix(): path for path in sorted(build_dir.rglob("*.html"))}


def _diff_page(a: HeadFields, b: HeadFields) -> list[str]:
    title_a, metas_a, links_a, ldjson_a = a
    title_b, metas_b, links_b, ldjson_b = b
    lines = []
    if title_a != title_b:
        lines.append(f"  title: {title_a!r} -> {title_b!r}")
    lines.extend(_set_diff("meta", metas_a, metas_b))
    lines.extend(_set_diff("link", links_a, links_b))
    lines.extend(_set_diff("JSON-LD", ldjson_a, ldjson_b))
    return lines


def _set_diff(label: str, a: tuple[str, ...], b: tuple[str, ...]) -> list[str]:
    only_a, only_b = sorted(set(a) - set(b)), sorted(set(b) - set(a))
    lines = [f"  {label} only in build-a: {item}" for item in only_a]
    lines.extend(f"  {label} only in build-b: {item}" for item in only_b)
    return lines


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("build_a", type=Path, help="a build's static-export root")
    parser.add_argument("build_b", type=Path, help="the other build's static-export root")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    for build_dir in (args.build_a, args.build_b):
        if not build_dir.is_dir():
            print(f"head_parity: {build_dir} is not a directory. Pass a build's static-export root.", file=sys.stderr)
            return 2

    pages_a = _discover_pages(args.build_a)
    pages_b = _discover_pages(args.build_b)
    print(f"Compared per page: {FIELDS_COMPARED}.")
    print(f"Pages compared: {len(set(pages_a) & set(pages_b))} (build-a: {len(pages_a)}, build-b: {len(pages_b)})")

    differences = 0
    for page in sorted(set(pages_a) - set(pages_b)):
        print(f"{page}: only in build-a")
        differences += 1
    for page in sorted(set(pages_b) - set(pages_a)):
        print(f"{page}: only in build-b")
        differences += 1
    for page in sorted(set(pages_a) & set(pages_b)):
        lines = _diff_page(extract_head(pages_a[page].read_text()), extract_head(pages_b[page].read_text()))
        if lines:
            differences += 1
            print(f"{page}:")
            for line in lines:
                print(line)

    if not differences:
        print("0 differences.")
        return 0
    print(f"{differences} pages differ.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
