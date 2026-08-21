#!/usr/bin/env python3
"""Regenerates `docs-site/content/reference/settings.mdx` and `reference/cli.mdx` from the
code that defines them.

Per the narrowed DS-D2 decision, `App`, the definition bases and the capability specs stay
hand-written prose  -  they describe behavior, not fields, so a generator would either flatten
that behavior away or grow into the docstring-mirror API reference the docs plan already
refused. Settings and the CLI are different: both are already structured data (`LayeredSettings`
subclasses in `agentdeck/runtime/settings.py`, the argparse tree in `agentdeck/cli.py`), so a
generator is a template renderer over data that already exists, not new authoring  -  and it is
the one thing that keeps a page this large from drifting out from under the code the way
hand-maintenance would.

    python scripts/generate_docs_reference.py            # (re)write both pages
    python scripts/generate_docs_reference.py --check    # exit 1 if a page would change

`tests/test_generated_reference.py` runs the `--check` path on every `make check`, so a settings
or CLI change that forgot to regenerate the docs fails the gate instead of shipping stale.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from contextlib import contextmanager
from pathlib import Path
from types import UnionType
from typing import TYPE_CHECKING, Any, get_args, get_origin

from agentdeck.cli import build_parser
from agentdeck.runtime import settings as settings_module
from agentdeck.runtime.settings import LayeredSettings

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from pydantic.fields import FieldInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
SITE = REPO_ROOT / "docs-site" / "content"
CONTENT = SITE / "reference"
SETTINGS_PAGE = CONTENT / "settings.mdx"
CLI_PAGE = CONTENT / "cli.mdx"
CHANGELOG_PAGE = SITE / "resources" / "changelog.mdx"
CHANGELOG_SOURCE = REPO_ROOT / "CHANGELOG.md"
PUBLIC = REPO_ROOT / "docs-site" / "public"
LLMS_PAGE = PUBLIC / "llms.txt"
LLMS_FULL_PAGE = PUBLIC / "llms-full.txt"
SITE_URL = "https://agentdecksdk.com"

_REPO_SETTINGS_URL = "https://github.com/agentdecksdk/agentdeck/blob/main/agentdeck/runtime/settings.py"
_REPO_CLI_URL = "https://github.com/agentdecksdk/agentdeck/blob/main/agentdeck/cli.py"


def _settings_classes() -> list[type[LayeredSettings]]:
    """Only the subclasses *defined in* ``settings.py``  -  not every ``LayeredSettings`` that
    happens to be loaded in the process. Nothing outside that module subclasses it today (the
    capability specs that used to did, and are gone), so the filter is currently a no-op  -  it
    stays because this page is the env-var table, and a subclass declared elsewhere for some
    other purpose must not land in it just because something imported it first.
    """
    return [cls for cls in LayeredSettings.__subclasses__() if cls.__module__ == settings_module.__name__]


def _type_str(annotation: Any) -> str:
    if annotation is type(None):
        return "None"
    origin = get_origin(annotation)
    if origin is UnionType:
        return " | ".join(_type_str(arg) for arg in get_args(annotation))
    if origin is not None:
        args = ", ".join(_type_str(arg) for arg in get_args(annotation))
        return f"{_type_str(origin)}[{args}]"
    return getattr(annotation, "__name__", str(annotation))


def _default_str(info: FieldInfo) -> str:
    if info.default_factory is not None:
        # Every default_factory in settings.py takes no arguments (dict/list style), so this
        # doesn't need the argument-forwarding pydantic itself supports for the rare factory
        # that takes validated data.
        value = info.default_factory()  # type: ignore[call-arg]
    elif info.is_required():
        return "*required*"
    else:
        value = info.default
    return f"`{value!r}`"


def _description(field_name: str, info: FieldInfo, cls: type[LayeredSettings]) -> str:
    if not info.description:
        raise ValueError(
            f"{cls.__name__}.{field_name} has no Field(description=...)  -  add one before regenerating "
            "the settings reference; a generator over an undescribed field would render a blank cell."
        )
    return info.description.replace("|", "\\|")


def _env_var(cls: type[LayeredSettings], field_name: str) -> str:
    # A field in ``_bare_env_names`` is read from that exact name, ignoring env_prefix  -  one
    # variable per decision (``AGENTDECK_EVENTS``), not ``<PREFIX>_<FIELD>``.
    if bare := cls._bare_env_names.get(field_name):
        return bare
    prefix = cls.model_config.get("env_prefix", "")
    return f"{prefix}{field_name}".upper()


def render_settings_mdx() -> str:
    lines = [
        "---",
        "title: Settings",
        "description: Every AGENTDECK_*, OPENAI_*, ANTHROPIC_*, GEMINI_*, OLLAMA_*, "
        "OPENROUTER_*, and TAVILY_* environment variable, "
        "generated from agentdeck/runtime/settings.py.",
        "---",
        "",
        "{/* docs_sources:",
        '  - "agentdeck/runtime/settings.py"',
        "*/}",
        "",
        "# Settings",
        "",
        f"Generated from [`agentdeck/runtime/settings.py`]({_REPO_SETTINGS_URL})'s `LayeredSettings` "
        "subclasses  -  this page cannot drift from the code because `make check` regenerates it and "
        "fails if the result differs (`scripts/generate_docs_reference.py`). Values come from process "
        "environment variables or the project-root `.env`; process values win.",
        "",
    ]
    for cls in _settings_classes():
        lines.append(f"## `{cls.__name__}`")
        lines.append("")
        # Docstrings across the codebase use RST-style double-backtick ``code`` (project
        # convention, not Sphinx-processed)  -  fold to single-backtick Markdown for MDX.
        summary = (cls.__doc__ or "").strip().splitlines()[0].strip().replace("``", "`") if cls.__doc__ else ""
        if summary:
            lines.append(summary)
            lines.append("")
        fields = cls.model_fields
        if not fields:
            lines.append(f"No declared fields  -  `{cls.__name__}` captures arbitrary env vars matching its prefix.")
            lines.append("")
            continue
        lines.append("| Env var | Type | Default | Description |")
        lines.append("|---|---|---|---|")
        for field_name, info in fields.items():
            env_var = _env_var(cls, field_name)
            # A bare "|" inside a table cell reads as the next column separator to GFM even
            # inside a code span, so a union type is rendered as separately-coded alternatives
            # joined by "or" rather than as one code span holding the literal "|".
            type_cell = " or ".join(f"`{part.strip()}`" for part in _type_str(info.annotation).split(" | "))
            default = _default_str(info)
            description = _description(field_name, info, cls)
            lines.append(f"| `{env_var}` | {type_cell} | {default} | {description} |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


@contextmanager
def _fixed_terminal_width(columns: int = 100) -> Iterator[None]:
    """Argparse's ``HelpFormatter`` wraps text to ``shutil.get_terminal_size()``, which reads
    ``COLUMNS`` before it ever queries a real tty  -  pin it so the generated CLI page renders
    identically on a developer's terminal and in CI instead of reflowing with whatever width
    happened to be ambient.
    """
    previous = os.environ.get("COLUMNS")
    os.environ["COLUMNS"] = str(columns)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("COLUMNS", None)
        else:
            os.environ["COLUMNS"] = previous


def _walk_parsers(
    parser: argparse.ArgumentParser, path: tuple[str, ...] = ()
) -> Iterator[tuple[tuple[str, ...], argparse.ArgumentParser]]:
    """Depth-first walk of the command tree, root first. Argparse has no public API for
    listing a parser's subparsers, so this reads ``_SubParsersAction``  -  the same private
    attribute tools like sphinx-argparse rely on for the same reason.
    """
    yield path, parser
    subparsers_action = next((a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None)
    if subparsers_action is not None:
        for name, sub in subparsers_action.choices.items():
            yield from _walk_parsers(sub, (*path, name))


def render_cli_mdx() -> str:
    lines = [
        "---",
        "title: CLI",
        "description: The agentdeck command tree, generated from agentdeck/cli.py's own --help output.",
        "---",
        "",
        "{/* docs_sources:",
        '  - "agentdeck/cli.py"',
        '  - "agentdeck/surfaces/cli/**"',
        "*/}",
        "",
        "# CLI",
        "",
        f"Generated from [`agentdeck/cli.py`]({_REPO_CLI_URL}) by capturing each subcommand's own "
        "`--help` output  -  the same rendering a terminal would show, not a second hand-written copy "
        "of it. `make check` regenerates this page and fails if the result differs "
        "(`scripts/generate_docs_reference.py`).",
        "",
    ]
    with _fixed_terminal_width():
        for path, node in _walk_parsers(build_parser()):
            command = "agentdeck" if not path else "agentdeck " + " ".join(path)
            lines.append(f"## `{command}`")
            lines.append("")
            lines.append("```text")
            lines.append(node.format_help().rstrip())
            lines.append("```")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


_RELEASE_HEADING = re.compile(r"^## \[([^\]]+)\](?: - (\S+))?\s*$", re.MULTILINE)
_RELEASES_URL = "https://github.com/agentdecksdk/agentdeck/releases"


def changelog_sections() -> list[tuple[str, str, str]]:
    """``(version, date, body)`` per ``## [x] - date`` heading in ``CHANGELOG.md``, in file order.

    Shared with the reference application's changelog tool, which needs the same parse over the
    same file  -  one heading regex, not two that drift.
    """
    text = CHANGELOG_SOURCE.read_text()
    heads = list(_RELEASE_HEADING.finditer(text))
    return [
        (m.group(1), m.group(2) or "", text[m.end() : (heads[i + 1].start() if i + 1 < len(heads) else len(text))])
        for i, m in enumerate(heads)
    ]


def render_changelog_mdx() -> str:
    """The site's release notes, rendered from ``CHANGELOG.md`` so the two cannot disagree.

    Only the current release is reproduced in full. Every earlier one is a row linking to its
    GitHub release, which is where the artifacts are anyway  -  a page restating 1,700 lines of
    history is not read, and the copy is one more thing to keep true.
    """
    sections = changelog_sections()
    unreleased = next(((v, b) for v, _d, b in sections if v.lower() == "unreleased"), None)
    released = [(v, d, b) for v, d, b in sections if v.lower() != "unreleased"]
    current, older = released[0], released[1:]

    out = [
        "---",
        "title: Changelog",
        "description: What changed in each release of AgentDeck, and what to do about it when upgrading.",
        "---",
        "",
        "{/* docs_sources:",
        '  - "CHANGELOG.md"',
        "*/}",
        "",
        "{/* Generated by scripts/generate_docs_reference.py from CHANGELOG.md  -  do not edit. */}",
        "",
        "# Changelog",
        "",
        "Rendered from the repository's own `CHANGELOG.md`, so this page cannot drift from it. Entries",
        "are written for someone using the package: what changed, and what to do about it.",
        "",
        f"The current release is **v{current[0]}**. Earlier releases are listed at the bottom.",
        "",
    ]
    if unreleased and unreleased[1].strip():
        out += ["## Unreleased", "", unreleased[1].strip(), ""]
    out += [f"## v{current[0]}", ""]
    if current[1]:
        out += [f"*Released {current[1]}.*", ""]
    out += [current[2].strip(), ""]
    if older:
        out += [
            "## Earlier releases",
            "",
            "| Version | Date | Notes |",
            "|---|---|---|",
            *(
                f"| [v{v}]({_RELEASES_URL}/tag/v{v}) | {d or ' - '} | [release notes]({_RELEASES_URL}/tag/v{v}) |"
                for v, d, _b in older
            ),
            "",
        ]
    return "\n".join(out).rstrip() + "\n"


_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_FIELD = re.compile(r"^(title|description):\s*(.+)$", re.MULTILINE)
_DOCS_SOURCES = re.compile(r"^\{/\* docs_sources:\n(?:  - .+\n)+\*/\}\n?", re.MULTILINE)

# A line that is nothing but a JSX component, which a page composed of components is mostly made
# of. Rendered they are the page; as text they are `<Hero />`, which is worse than nothing to a
# retrieval system. Stripped rather than escaped, because there is no prose in them to keep.
_JSX_ONLY = re.compile(r'^[ \t]*<(?:[A-Z][A-Za-z0-9]*|div className="[^"]*")[^>]*/>[ \t]*$\n?', re.MULTILINE)

# What AgentDeck is, in the words a retrieval system should quote. Deliberately names the
# frameworks it wraps and the problem it solves, because that is what someone searching a
# *symptom*  -  "pause an AI agent", "durable approval"  -  needs to match against.
_DEFINITION = """\
AgentDeck SDK is a Python harness for agents you have to operate. You write agents, workflows and
skills as small declarations in a `.agentdeck/` directory; AgentDeck supplies everything around
them  -  discovery, layered settings, sessions, streaming, MCP servers, durable human-in-the-loop
approvals, run control, and one ordered event log per run.

It wraps the OpenAI Agents SDK rather than replacing it: an `Agent` compiles to an SDK agent,
and a `@workflow` is ordinary Python awaited by AgentDeck's own executor. AgentDeck owns
configuration; the SDK owns execution. There is no agent loop here.

Use it when the wiring around an agent has become the work: several agents and workflows in one
project, a chat surface and a batch path over the same definitions, runs you must inspect
afterwards, approvals that outlive the process that requested them."""


def _page_text(path: Path, generated: Mapping[Path, str] | None = None) -> str:
    """Read a page, preferring content rendered earlier in this generator pass."""
    return generated[path] if generated and path in generated else path.read_text()


def _page_meta(path: Path, generated: Mapping[Path, str] | None = None) -> tuple[str, str]:
    """``(title, description)`` from a page's frontmatter, falling back to the slug."""
    found = _FRONTMATTER.search(_page_text(path, generated))
    fields = dict(_FIELD.findall(found.group(1))) if found else {}
    return fields.get("title", path.stem), fields.get("description", "")


def _site_pages() -> list[tuple[str, Path]]:
    """``(slug, path)`` for every published page, in a reading order rather than alphabetical."""
    root = SITE
    order = [
        "index",
        "getting-started",
        "concepts",
        "guides",
        "operating",
        "reference",
        "roadmap",
        "known-issues",
        "changelog",
    ]

    def rank(slug: str) -> tuple[int, str]:
        head = slug.split("/")[0]
        return (order.index(head) if head in order else len(order), slug)

    pages = []
    for path in sorted(root.rglob("*.mdx")):
        rel = path.relative_to(root).with_suffix("")
        slug = "index" if str(rel) == "index" else (str(rel.parent) if rel.name == "index" else str(rel))
        pages.append((slug, path))
    return sorted(pages, key=lambda pair: rank(pair[0]))


def render_llms_txt(generated: Mapping[Path, str] | None = None) -> str:
    """`/llms.txt`  -  the llmstxt.org convention: what this is, then every page as a link.

    Small on purpose. A model or crawler reads this to decide *which* page to fetch; the whole
    corpus is `llms-full.txt`, and conflating them makes the cheap file expensive.
    """
    out = [
        "# AgentDeck SDK",
        "",
        "> The production runtime for agents you already have  -  "
        "composition, durable human-in-the-loop approvals, sessions, streaming, run control "
        "and one ordered event log, wrapping the OpenAI Agents SDK.",
        "",
        _DEFINITION,
        "",
        "## Documentation",
        "",
    ]
    for slug, path in _site_pages():
        title, description = _page_meta(path, generated)
        url = f"{SITE_URL}/{'' if slug == 'index' else slug}"
        out.append(f"- [{title}]({url}){': ' + description if description else ''}")
    out += [
        "",
        "## Source",
        "",
        "- [Repository](https://github.com/agentdecksdk/agentdeck): issues, releases and examples",
        f"- [Full documentation as one file]({SITE_URL}/llms-full.txt)",
        "",
    ]
    return "\n".join(out)


def render_llms_full_txt(generated: Mapping[Path, str] | None = None) -> str:
    """`/llms-full.txt`  -  every page's Markdown in one file, in reading order.

    Frontmatter becomes a heading and a line of prose so the file reads as a document rather than
    as a concatenation, and each page keeps its URL so a quotation can be traced back.
    """
    out = [
        "# AgentDeck SDK  -  full documentation",
        "",
        _DEFINITION,
        "",
        f"Source: {SITE_URL} · Generated from the same Markdown the site renders.",
        "",
        "---",
        "",
    ]
    for slug, path in _site_pages():
        page = _page_text(path, generated)
        title, description = _page_meta(path, generated)
        body = _DOCS_SOURCES.sub("", _JSX_ONLY.sub("", _FRONTMATTER.sub("", page, count=1))).strip()
        url = f"{SITE_URL}/{'' if slug == 'index' else slug}"
        out += [f"# {title}", "", f"*{description}*" if description else "", f"Source: {url}", "", body, "", "---", ""]
    # The loop leaves a separator and a blank line after the last page, which `end-of-file-fixer`
    # strips on every commit and this generator restores on every run  -  a hook and a generator
    # fighting over one byte. One trailing newline, settled here.
    return "\n".join(out).rstrip("\n") + "\n"


def _generated_pages() -> dict[Path, str]:
    pages = {
        SETTINGS_PAGE: render_settings_mdx(),
        CLI_PAGE: render_cli_mdx(),
        CHANGELOG_PAGE: render_changelog_mdx(),
    }
    pages[LLMS_PAGE] = render_llms_txt(pages)
    pages[LLMS_FULL_PAGE] = render_llms_full_txt(pages)
    return pages


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    check = "--check" in args
    pages = _generated_pages()
    if check:
        drifted = [path for path, content in pages.items() if not path.is_file() or path.read_text() != content]
        if drifted:
            print(
                "generated reference pages are stale  -  run `python scripts/generate_docs_reference.py`:",
                file=sys.stderr,
            )
            for path in drifted:
                print(f"  {path}", file=sys.stderr)
            return 1
        return 0
    CONTENT.mkdir(parents=True, exist_ok=True)
    PUBLIC.mkdir(parents=True, exist_ok=True)
    for path, content in pages.items():
        path.write_text(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CHANGELOG_PAGE",
    "LLMS_FULL_PAGE",
    "LLMS_PAGE",
    "CHANGELOG_SOURCE",
    "CLI_PAGE",
    "SETTINGS_PAGE",
    "changelog_sections",
    "main",
    "render_changelog_mdx",
    "render_llms_full_txt",
    "render_llms_txt",
    "render_cli_mdx",
    "render_settings_mdx",
]
