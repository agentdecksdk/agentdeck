"""Anti-rot checks for the published docs (docs/delivery/docs-site-plan.md §6).

Blocks are parsed and their agentdeck imports resolved, not executed: the golden suite's
scripted model is hard-wired to one two-turn conversation, so executing arbitrary examples
needs a generalised fake provider — that harness is DS-1's opening deliverable.
"""

import importlib
import re
from pathlib import Path

import pytest

CONTENT = Path(__file__).resolve().parents[1] / "docs-site" / "content"
FENCE = re.compile(r"^```(\w+)([^\n]*)\n(.*?)^```", re.MULTILINE | re.DOTALL)
IMPORT = re.compile(r"^\s*(?:from\s+(agentdeck[\w.]*)\s+import\s+([^\n#]+)|import\s+(agentdeck[\w.]*))", re.MULTILINE)
LINK = re.compile(r"\]\((/[^)\s]*)\)")
REASON = re.compile(r'reason="[^"]+"')


def _pages() -> list[Path]:
    pages = sorted(CONTENT.rglob("*.mdx"))
    assert pages, f"no .mdx pages under {CONTENT} — the content dir moved"
    return pages


@pytest.mark.parametrize("page", _pages(), ids=lambda p: p.name)
def test_python_blocks_parse_and_their_agentdeck_imports_resolve(page: Path) -> None:
    for lang, meta, src in FENCE.findall(page.read_text()):
        if lang != "python":
            continue
        if "no-test" in meta:
            assert REASON.search(meta), f'{page.name}: no-test needs reason="why this block cannot run"'
            continue
        compile(src, str(page), "exec")
        for module, names, plain in IMPORT.findall(src):
            target = importlib.import_module(module or plain)
            for name in (n.strip() for n in names.split(",") if n.strip()):
                assert hasattr(target, name), f"{page.name}: {module}.{name} does not exist"


@pytest.mark.parametrize("page", _pages(), ids=lambda p: p.name)
def test_internal_links_resolve_to_a_page(page: Path) -> None:
    for href in LINK.findall(page.read_text()):
        slug = href.split("#")[0].strip("/")
        if not slug:
            continue
        assert (CONTENT / f"{slug}.mdx").exists() or (CONTENT / slug / "index.mdx").exists(), (
            f"{page.name}: link /{slug} has no page"
        )
