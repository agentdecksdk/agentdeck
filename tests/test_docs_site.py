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
# Top-level keys only (exactly one indent): a Nextra separator entry is an object, and its
# inner `type:`/`title:` are not nav entries.
META_KEY = re.compile(r"^  '?([\w-][\w -]*)'?:", re.MULTILINE)
PYTHON = {"python", "py"}
REASON = re.compile(r'reason="[^"]+"')


@cache
def _pages() -> tuple[Path, ...]:
    pages = tuple(sorted(CONTENT.rglob("*.mdx")))
    assert pages, f"no .mdx pages under {CONTENT} — the content dir moved"
    return pages


# `changelog.mdx` is generated from `CHANGELOG.md`, and a changelog's content is *history*: it
# names `agentdeck.adapters.caps` and `openai_agents.structured_output` precisely because it is
# recording that they were deleted, and it quotes snippets from releases whose API is gone. The
# two content checks below would flag every one as rot, which means either a permanently red gate
# or a changelog edited to hide what it exists to record.
#
# Not unchecked, just checked elsewhere: `test_generated_reference.py` asserts the page still
# matches `CHANGELOG.md` exactly, so its correctness is anchored to the source file rather than to
# the current shape of the package.
HISTORY = {"changelog.mdx"}


@cache
def _authored_pages() -> tuple[Path, ...]:
    """Pages whose prose is a claim about the package *as it is now*."""
    return tuple(page for page in _pages() if page.name not in HISTORY)


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


@pytest.mark.parametrize("page", _authored_pages(), ids=lambda p: p.name)
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
    keys = {key for key in META_KEY.findall(meta.read_text()) if not key.startswith("--")}
    assert keys == entries, f"{meta.parent.name}/_meta.ts and its pages disagree"


# A backticked `Thing.attr` in prose, e.g. `App.chat` or `app.store`. Bare names are skipped:
# `run` or `output` are ordinary English here, and checking them finds nothing but noise.
DOTTED = re.compile(r"`([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)+)`")


@cache
def _package_source() -> str:
    root = Path(__file__).resolve().parents[1] / "agentdeck"
    return "\n".join(path.read_text() for path in root.rglob("*.py"))


def _prose(page: Path) -> str:
    return FENCE.sub("", page.read_text())


@pytest.mark.parametrize("page", _authored_pages(), ids=lambda p: p.name)
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

    `context7.json` is checked for the same reason and is the worst place for a stale pin: its
    rules are fed to coding agents as ground truth, so a wrong version there is retyped into
    other people's terminals rather than merely read. Only the stale-pin half applies to it —
    it has no fenced blocks, so the unpinned-install check below cannot see it.
    """
    import tomllib

    root = Path(__file__).resolve().parents[1]
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    # `[\w.]` rather than "anything but a quote": in JSON the pin is written `@v3.0.1\"`, and a
    # looser class swallows the escaping backslash into the captured version.
    pin = re.compile(r"agentdeck(?:\.git)?@v([0-9][\w.]*)")
    documents = (*_pages(), *_repo_markdown(), root / "context7.json")

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


# Both live origins. `agentdecksdk.com` is canonical and is what prose should link, but the Pages
# mirror is a real URL a reader can land on, so a stale link there is a real 404 — and the last
# rewrite of these links (owner `sagi5060` -> `agentdecksdk`) is exactly the kind of sweep that
# leaves one behind. Matching both means neither form can rot unnoticed.
SITE_LINK = re.compile(r"https://(?:agentdecksdk\.com|agentdecksdk\.github\.io/agentdeck)/([\w/-]*)")


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


# Context7's own limits, discovered from a rejected submission rather than from its schema, which
# declares neither. Encoded here because the failure is silent and total: an over-length field does
# not truncate, it invalidates the whole file, and Context7 then indexes the repository with its
# defaults — no rules, no folder scoping — while reporting the submission as successful.
CONTEXT7_LIMITS = {"description": 200, "rule": 255}


def test_context7_manifest_stays_within_the_limits_that_reject_it() -> None:
    import json

    manifest = json.loads((ROOT / "context7.json").read_text())
    fields = [("description", manifest["description"], CONTEXT7_LIMITS["description"])]
    fields += [(f"rules.{i}", rule, CONTEXT7_LIMITS["rule"]) for i, rule in enumerate(manifest["rules"])]
    over = [f"{name}: {len(text)} > {limit} — {text[:60]}…" for name, text, limit in fields if len(text) > limit]
    assert not over, "context7.json would be rejected:\n  " + "\n  ".join(over)


def test_context7_excludes_are_bare_filenames() -> None:
    """`excludeFiles` matches on filename only. A path there excludes nothing, silently — which is
    how `changelog.mdx` was almost fed to coding agents as current API rather than as history.
    """
    import json

    manifest = json.loads((ROOT / "context7.json").read_text())
    with_paths = [name for name in manifest["excludeFiles"] if "/" in name]
    assert not with_paths, f"context7.json excludeFiles must be bare filenames, not paths: {with_paths}"
