"""The brand kit is copied, not referenced, so this is what keeps the copies honest.

Cross-file composition was measured and rejected: `<use href="other.svg#id">` renders blank
under default Chrome flags, and even a same-document `<use>` disappears when the nested `<svg>`
it sits in carries a viewBox tighter than the definition's own space. Both fail silently, into a
PNG nobody re-checks. So every card carries its own copy of the geometry, `docs/brand/components/`
holds the one each was copied from, and these tests fail the moment a copy stops matching.
"""

import re
from functools import cache
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "docs" / "brand"
COMPONENTS = BRAND / "components"
PATH_D = re.compile(r'\sd="([^"]+)"')
FILL = re.compile(r'fill="(#[0-9a-fA-F]{3,8})"')
PALETTE_CSS = ROOT / "docs-site" / "app" / "brand.css"
PALETTE_TOKEN = re.compile(r"(--brand-[\w-]+)\s*:\s*(#[0-9a-fA-F]{3,8})")
WORD = "agentdeck"

# The designer's trace is history, not a consumer: freezing it against a component would mean
# editing provenance whenever the geometry it records is revised.
PROVENANCE = {"logo-traced-original.svg"}


@cache
def _compositions() -> tuple[Path, ...]:
    """Everything assembled from the parts, rather than being one.

    The two docs-site files are here because they are compositions that happen not to live in
    `docs/brand/`: the navbar mark inlines the card as TSX so it can inherit `currentColor`, and
    Next serves the favicon from `app/icon.svg` by filename convention. Both were hand-copies,
    both were still on the pre-refactor coordinates, and nothing was watching either.
    """
    files = tuple(svg for svg in sorted(BRAND.glob("*.svg")) if svg.name not in PROVENANCE)
    assert files, f"no brand SVGs under {BRAND} — the directory moved"
    site = (ROOT / "docs-site" / "app" / "mark.tsx", ROOT / "docs-site" / "app" / "icon.svg")
    assert all(p.is_file() for p in site), f"a docs-site brand file moved: {site}"
    return files + site


@cache
def _canonical() -> frozenset[str]:
    paths = frozenset(d for part in COMPONENTS.glob("*.svg") for d in PATH_D.findall(part.read_text()))
    assert paths, f"no path geometry under {COMPONENTS} — the components moved"
    return paths


@pytest.mark.parametrize("svg", _compositions(), ids=lambda svg: svg.stem)
def test_composition_carries_only_component_geometry(svg: Path) -> None:
    """A card that drifts from its part is the failure copy-paste actually has, and it is invisible:
    the SVG still renders, just fractionally wrong, and only against the other cards does it show.

    Stated as "every path here came from a part" rather than "every part appears intact here". The
    second phrasing needs a token to decide which file uses which part, and editing that token is
    then the one edit the check cannot see.
    """
    for path in PATH_D.findall(svg.read_text()):
        assert path in _canonical(), (
            f"{svg.name} carries geometry that is in no component: re-copy the <g> from "
            f"docs/brand/components/, do not hand-edit a path in place"
        )


@pytest.mark.parametrize("svg", _compositions(), ids=lambda svg: svg.stem)
def test_composition_paints_only_in_palette_colours(svg: Path) -> None:
    """An off-palette colour does not look wrong, it looks almost right. The A counter sat at
    `#ffffff` next to a `#fafbfe` headline for exactly that reason: two whites, one card, and
    nothing to tell you which was the brand's.
    """
    palette = {value.lower() for _, value in PALETTE_TOKEN.findall(PALETTE_CSS.read_text())}
    assert palette, f"no --brand-* tokens in {PALETTE_CSS} — the palette moved"

    strays = {fill.lower() for fill in FILL.findall(svg.read_text())} - palette
    assert not strays, f"{svg.name} paints in {sorted(strays)}, which is in no --brand-* token"


def test_the_wordmark_is_outlines_everywhere() -> None:
    """Set live, the wordmark is a font dependency: a renderer without Poppins substitutes rather
    than failing, so the card still comes out, in the wrong face, and the PNG ships that way.
    Outlines carry the weight and the tracking as geometry, which the test above then pins.
    """
    assert len(PATH_D.findall((COMPONENTS / "wordmark.svg").read_text())) == len(WORD), (
        f"wordmark.svg should be one outline per letter of `{WORD}`"
    )
    live = [svg.name for svg in _compositions() if f">{WORD}</text>" in svg.read_text()]
    assert not live, f"the wordmark is set as live text in: {live}"
