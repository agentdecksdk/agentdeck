"""Unit coverage for `scripts/head_parity.py`'s parse/diff logic against synthetic fixtures.

The acceptance proof that the tool catches a real regression lives in the PR body's Verification
table (built trees, not fixtures): PR #689's first head vs current `dev` reports 47/47 pages
differing on the exact defect (`theme-color` missing, `description` missing on 14 pages) that
scratchpad script was rebuilt to catch. This file only covers the pure functions that make that
possible, so a change to the extractor or the diff logic fails fast without a two-build round trip.
"""

from __future__ import annotations

from head_parity import _diff_page, extract_head


def _head(body: str) -> str:
    return f"<!DOCTYPE html><html><head>{body}</head><body></body></html>"


def test_identical_heads_produce_no_diff() -> None:
    html = _head(
        '<title>A</title><meta name="description" content="d"/>'
        '<link rel="canonical" href="https://x/"/>'
        '<script type="application/ld+json">{"b": 2, "a": 1}</script>'
    )
    assert _diff_page(extract_head(html), extract_head(html)) == []


def test_a_missing_meta_tag_is_reported() -> None:
    a = extract_head(_head('<meta name="theme-color" content="light"/>'))
    b = extract_head(_head(""))
    lines = _diff_page(a, b)
    assert any("theme-color" in line and "only in build-a" in line for line in lines)


def test_a_changed_title_is_reported() -> None:
    a = extract_head(_head("<title>Old</title>"))
    b = extract_head(_head("<title>New</title>"))
    assert _diff_page(a, b) == ["  title: 'Old' -> 'New'"]


def test_json_ld_reformatted_with_different_key_order_is_not_a_diff() -> None:
    a = extract_head(_head('<script type="application/ld+json">{"a": 1, "b": 2}</script>'))
    b = extract_head(_head('<script type="application/ld+json">{"b": 2, "a": 1}</script>'))
    assert _diff_page(a, b) == []


def test_json_ld_value_change_is_reported() -> None:
    a = extract_head(_head('<script type="application/ld+json">{"a": 1}</script>'))
    b = extract_head(_head('<script type="application/ld+json">{"a": 2}</script>'))
    lines = _diff_page(a, b)
    assert lines and "JSON-LD" in lines[0]


def test_a_canonical_link_change_is_reported() -> None:
    a = extract_head(_head('<link rel="canonical" href="https://x/a"/>'))
    b = extract_head(_head('<link rel="canonical" href="https://x/b"/>'))
    lines = _diff_page(a, b)
    assert any("only in build-a" in line for line in lines) and any("only in build-b" in line for line in lines)


def test_a_non_canonical_link_is_ignored() -> None:
    a = extract_head(_head('<link rel="stylesheet" href="/a.css"/>'))
    b = extract_head(_head('<link rel="stylesheet" href="/b.css"/>'))
    assert _diff_page(extract_head(_head("")), a) == []
    assert _diff_page(a, b) == []
