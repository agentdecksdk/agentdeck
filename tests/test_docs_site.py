"""Anti-rot checks for the published docs (docs/delivery/docs-site-plan.md §6).

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
