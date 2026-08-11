#!/usr/bin/env python3
"""Regenerates `docs-site/content/reference/settings.mdx` and `reference/cli.mdx` from the
code that defines them.

Per the narrowed DS-D2 decision, `App`, the definition bases and the capability specs stay
hand-written prose — they describe behavior, not fields, so a generator would either flatten
that behavior away or grow into the docstring-mirror API reference the docs plan already
refused. Settings and the CLI are different: both are already structured data (`LayeredSettings`
subclasses in `agentdeck/runtime/settings.py`, the argparse tree in `agentdeck/cli.py`), so a
generator is a template renderer over data that already exists, not new authoring — and it is
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
import sys
from contextlib import contextmanager
from pathlib import Path
from types import UnionType
from typing import TYPE_CHECKING, Any, get_args, get_origin

from agentdeck.cli import build_parser
from agentdeck.runtime import settings as settings_module
from agentdeck.runtime.settings import LayeredSettings

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pydantic.fields import FieldInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTENT = REPO_ROOT / "docs-site" / "content" / "reference"
SETTINGS_PAGE = CONTENT / "settings.mdx"
CLI_PAGE = CONTENT / "cli.mdx"

_REPO_SETTINGS_URL = "https://github.com/sagi5060/agentdeck/blob/main/agentdeck/runtime/settings.py"
_REPO_CLI_URL = "https://github.com/sagi5060/agentdeck/blob/main/agentdeck/cli.py"


def _settings_classes() -> list[type[LayeredSettings]]:
    """Only the subclasses *defined in* ``settings.py`` — not every ``LayeredSettings`` that
    happens to be loaded in the process. Nothing outside that module subclasses it today (the
    capability specs that used to did, and are gone), so the filter is currently a no-op — it
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
            f"{cls.__name__}.{field_name} has no Field(description=...) — add one before regenerating "
            "the settings reference; a generator over an undescribed field would render a blank cell."
        )
    return info.description.replace("|", "\\|")


def _env_var(cls: type[LayeredSettings], field_name: str) -> str:
    # A field in ``_bare_env_names`` is read from that exact name, ignoring env_prefix — one
    # variable per decision (``AGENTDECK_EVENTS``), not ``<PREFIX>_<FIELD>``.
    if bare := cls._bare_env_names.get(field_name):
        return bare
    prefix = cls.model_config.get("env_prefix", "")
    return f"{prefix}{field_name}".upper()


def render_settings_mdx() -> str:
    lines = [
        "---",
        "title: Settings",
        "description: Every AGENTDECK_* (and OPENAI_*/TAVILY_*) environment variable, "
        "generated from agentdeck/runtime/settings.py.",
        "---",
        "",
        "# Settings",
        "",
        f"Generated from [`agentdeck/runtime/settings.py`]({_REPO_SETTINGS_URL})'s `LayeredSettings` "
        "subclasses — this page cannot drift from the code because `make check` regenerates it and "
        "fails if the result differs (`scripts/generate_docs_reference.py`). Every variable is also "
        "settable in the shared `config.yaml`, under the section derived from its env-var prefix "
        "(`openai:`, `runner:`, …); an env var wins over the file.",
        "",
    ]
    for cls in _settings_classes():
        lines.append(f"## `{cls.__name__}`")
        lines.append("")
        # Docstrings across the codebase use RST-style double-backtick ``code`` (project
        # convention, not Sphinx-processed) — fold to single-backtick Markdown for MDX.
        summary = (cls.__doc__ or "").strip().splitlines()[0].strip().replace("``", "`") if cls.__doc__ else ""
        if summary:
            lines.append(summary)
            lines.append("")
        fields = cls.model_fields
        if not fields:
            lines.append(f"No declared fields — `{cls.__name__}` captures arbitrary env vars matching its prefix.")
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
    ``COLUMNS`` before it ever queries a real tty — pin it so the generated CLI page renders
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
    listing a parser's subparsers, so this reads ``_SubParsersAction`` — the same private
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
        "# CLI",
        "",
        f"Generated from [`agentdeck/cli.py`]({_REPO_CLI_URL}) by capturing each subcommand's own "
        "`--help` output — the same rendering a terminal would show, not a second hand-written copy "
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


def _generated_pages() -> dict[Path, str]:
    return {SETTINGS_PAGE: render_settings_mdx(), CLI_PAGE: render_cli_mdx()}


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    check = "--check" in args
    pages = _generated_pages()
    if check:
        drifted = [path for path, content in pages.items() if not path.is_file() or path.read_text() != content]
        if drifted:
            print(
                "generated reference pages are stale — run `python scripts/generate_docs_reference.py`:",
                file=sys.stderr,
            )
            for path in drifted:
                print(f"  {path}", file=sys.stderr)
            return 1
        return 0
    CONTENT.mkdir(parents=True, exist_ok=True)
    for path, content in pages.items():
        path.write_text(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["CLI_PAGE", "SETTINGS_PAGE", "main", "render_cli_mdx", "render_settings_mdx"]
