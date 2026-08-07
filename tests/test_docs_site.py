"""Anti-rot checks for the published docs: every code sample on the site must keep working.

Python blocks are parsed and their agentdeck imports resolved, not executed: the golden
suite's scripted model is hard-wired to one two-turn conversation, so executing arbitrary
examples needs a generalised fake provider — that harness is DS-1's opening deliverable.
"""

import ast
import importlib
import re
from functools import cache
from pathlib import Path

import pytest

CONTENT = Path(__file__).resolve().parents[1] / "docs-site" / "content"
FENCE = re.compile(r"^[ \t]*```(\w+)([^\n]*)\n(.*?)^[ \t]*```", re.MULTILINE | re.DOTALL)
# Absolute markdown links only: relative hrefs, reference-style links and MDX <Cards> are invisible here.
LINK = re.compile(r"\]\((/[^)\s]*)\)")
META_KEY = re.compile(r"^\s+'?([\w-]+)'?:", re.MULTILINE)
PYTHON = {"python", "py"}
REASON = re.compile(r'reason="[^"]+"')


@cache
def _pages() -> tuple[Path, ...]:
    pages = tuple(sorted(CONTENT.rglob("*.mdx")))
    assert pages, f"no .mdx pages under {CONTENT} — the content dir moved"
    return pages


def _assert_agentdeck_imports_exist(src: str, page: Path) -> None:
    for node in ast.walk(ast.parse(src, str(page))):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("agentdeck"):
            module = importlib.import_module(node.module or "")
            for alias in node.names:
                assert hasattr(module, alias.name), f"{page.name}: {node.module}.{alias.name} does not exist"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("agentdeck"):
                    importlib.import_module(alias.name)


@pytest.mark.parametrize("page", _pages(), ids=lambda p: p.name)
def test_python_blocks_parse_and_their_agentdeck_imports_resolve(page: Path) -> None:
    for lang, meta, src in FENCE.findall(page.read_text()):
        if lang not in PYTHON:
            continue
        if "no-test" in meta:
            assert REASON.search(meta), f'{page.name}: no-test needs reason="why this block cannot run"'
            continue
        _assert_agentdeck_imports_exist(src, page)


@pytest.mark.parametrize("page", _pages(), ids=lambda p: p.name)
def test_internal_links_resolve_to_a_page(page: Path) -> None:
    for href in LINK.findall(page.read_text()):
        slug = href.split("#")[0].strip("/")
        if not slug:
            continue
        assert (CONTENT / f"{slug}.mdx").exists() or (CONTENT / slug / "index.mdx").exists(), (
            f"{page.name}: link /{slug} has no page"
        )


@pytest.mark.parametrize("meta", sorted(CONTENT.rglob("_meta.ts")), ids=lambda p: str(p.parent.name))
def test_nav_keys_match_pages_in_every_section(meta: Path) -> None:
    section = meta.parent
    entries = {p.stem for p in section.glob("*.mdx")} | {d.name for d in section.iterdir() if d.is_dir()}
    assert set(META_KEY.findall(meta.read_text())) == entries, f"{meta.parent.name}/_meta.ts and its pages disagree"


# A backticked `Thing.attr` in prose, e.g. `App.chat` or `app.store`. Bare names are skipped:
# `run` or `output` are ordinary English here, and checking them finds nothing but noise.
DOTTED = re.compile(r"`([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)+)`")


@cache
def _package_source() -> str:
    root = Path(__file__).resolve().parents[1] / "agentdeck"
    return "\n".join(path.read_text() for path in root.rglob("*.py"))


def _prose(page: Path) -> str:
    return FENCE.sub("", page.read_text())


@pytest.mark.parametrize("page", _pages(), ids=lambda p: p.name)
def test_dotted_names_in_prose_still_exist_in_the_package(page: Path) -> None:
    """Prose naming something the code no longer has is the site's most repeated defect —
    a settings description outlived the mechanism it described by eleven days, and two pages
    outlived an `App` change by one. Fences are already executed; this covers the sentences.

    Only catches names that vanished. A sentence that is wrong *about a name that still
    exists* reads exactly like a correct one, and nothing here will tell you.
    """
    source = _package_source()
    missing = sorted({name for name in DOTTED.findall(_prose(page)) if name.split(".")[-1] not in source})
    assert not missing, f"{page.name}: named in prose but absent from agentdeck/: {missing}"
