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

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "docs-site" / "content"
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


@cache
def _repo_markdown() -> tuple[Path, ...]:
    """The prose outside the site that a reader still meets first: the README (which is also the
    package's PyPI description) and the copyable decks under ``examples/``. Same rot, same
    checks, and nothing else was looking at them.
    """
    files = (ROOT / "README.md", *sorted(ROOT.glob("examples/*/README.md")))
    assert all(path.is_file() for path in files), f"a repo markdown file moved: {files}"
    return files


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


INSTALL_LINE = re.compile(r"\b(?:pip install|uv add|pipx install)\b.*\bagentdeck\b", re.IGNORECASE)


def test_pinned_install_versions_match_the_package_version() -> None:
    """Every agentdeck install line on the site, in the README, or in an example must carry a
    `git+...@vX.Y.Z` pin naming the version this tree actually is.

    Nothing else catches a stale *or missing* pin: the fence checks above parse Python, and
    `docs-check.yml` only confirms a page was produced. A stale pin has shipped three times,
    most recently telling beta users to install v2.0.0 while reading v3 docs; an *unqualified*
    install (no pin at all) is the same failure by omission — `agentdeck[serve]` with no `@v...`
    resolves to whatever a fresh install picks, not the version the page's own examples were
    written against. Only fenced shell blocks count — an install line mentioned in prose (e.g. as
    a contrast, "not something `pip install agentdeck` gives you") is not an instruction to run.
    """
    import tomllib

    root = Path(__file__).resolve().parents[1]
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    pin = re.compile(r"agentdeck(?:\.git)?@v([0-9][^\"'\s)]*)")
    documents = (*_pages(), *_repo_markdown())

    stale = [
        f"{page.relative_to(root)}: pins v{found} but pyproject says {version}"
        for page in documents
        for found in pin.findall(page.read_text())
        if found != version
    ]
    stale += [
        f"{page.relative_to(root)}: unpinned agentdeck install in a `{lang}` block — {line.strip()!r}"
        for page in documents
        for lang, _meta, body in FENCE.findall(page.read_text())
        if lang == "bash"
        for line in body.splitlines()
        if INSTALL_LINE.search(line) and not pin.search(line)
    ]
    assert not stale, "stale or unpinned install pin(s):\n  " + "\n  ".join(stale)


@pytest.mark.parametrize("document", _repo_markdown(), ids=lambda p: str(p))
def test_python_fences_in_repo_markdown_resolve(document: Path) -> None:
    """The README and the example READMEs get the same stage-1 treatment as the site's pages:
    parsed, with every name they import from `agentdeck` confirmed to exist.
    """
    for lang, _meta, src in FENCE.findall(document.read_text()):
        if lang in PYTHON:
            _assert_agentdeck_imports_exist(src, document)


SITE_LINK = re.compile(r"https://sagi5060\.github\.io/agentdeck/([\w/-]*)")


@pytest.mark.parametrize("document", _repo_markdown(), ids=lambda p: str(p))
def test_docs_site_links_in_repo_markdown_reach_a_real_page(document: Path) -> None:
    """A README links the published site by absolute URL, which no build step resolves — a page
    renamed on the site leaves a 404 behind in the one file most readers start from.
    """
    for slug in SITE_LINK.findall(document.read_text()):
        slug = slug.strip("/")
        assert not slug or (CONTENT / f"{slug}.mdx").exists() or (CONTENT / slug / "index.mdx").exists(), (
            f"{document.name}: docs-site link /{slug} has no page"
        )


def test_every_public_deck_method_is_documented_somewhere() -> None:
    """`Deck` is the API this release exists to offer, so a public name absent from the whole
    site is undiscoverable — `asgi()` was, and it is how you serve a deck.

    Introspects the class rather than grepping source, so a `def` inside a docstring example
    cannot be mistaken for surface.
    """
    from agentdeck import Deck

    documented = " ".join(page.read_text() for page in _pages())
    missing = sorted(name for name in vars(Deck) if not name.startswith("_") and name not in documented)
    assert not missing, f"public Deck names documented nowhere on the site: {missing}"
