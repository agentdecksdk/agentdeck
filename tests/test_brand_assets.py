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
TEXT_ATTRS = re.compile(r"<text\b([^>]*)>agentdeck</text>")

# The designer's trace is history, not a consumer: freezing it against a component would mean
# editing provenance whenever the geometry it records is revised.
PROVENANCE = {"logo-traced-original.svg"}


@cache
def _compositions() -> tuple[Path, ...]:
    """Every brand SVG that is assembled from the parts, rather than being one."""
    files = tuple(svg for svg in sorted(BRAND.glob("*.svg")) if svg.name not in PROVENANCE)
    assert files, f"no brand SVGs under {BRAND} — the directory moved"
    return files


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


def test_the_wordmark_keeps_its_tracking() -> None:
    """Retyping the wordmark is what loses the tracking, and at a glance nothing looks wrong."""
    spec = TEXT_ATTRS.search((COMPONENTS / "wordmark.svg").read_text())
    assert spec, "wordmark.svg no longer renders `agentdeck` as text"
    ratio = _tracking(spec.group(1))

    for svg in _compositions():
        for attrs in TEXT_ATTRS.findall(svg.read_text()):
            assert "Poppins" in attrs and 'font-weight="700"' in attrs, (
                f"{svg.name} sets the wordmark in something other than Poppins 700"
            )
            assert abs(_tracking(attrs) - ratio) < 1e-4, (
                f"{svg.name} tracks the wordmark at {_tracking(attrs):.4f}em, not {ratio:.4f}em"
            )


def _tracking(attrs: str) -> float:
    """`letter-spacing` is absolute in SVG, so only its ratio to `font-size` survives a resize."""
    size = re.search(r'font-size="([\d.]+)"', attrs)
    spacing = re.search(r'letter-spacing="(-?[\d.]+)"', attrs)
    assert size and spacing, f"wordmark text carries no size or tracking: {attrs.strip()}"
    return float(spacing.group(1)) / float(size.group(1))
