"""``coerce_input`` — the one place a bare string becomes an ``Input``."""

from __future__ import annotations

import pytest

from agentdeck.core import ImageBlock, ResourceBlock, TextBlock, coerce_input


def test_string_becomes_one_text_block():
    assert coerce_input("hi") == [TextBlock(text="hi")]


def test_input_passes_through_unchanged():
    blocks = [TextBlock(text="hi"), ImageBlock(media_type="image/png", data_b64="AA=="), ResourceBlock(uri="s3://b/k")]
    assert coerce_input(blocks) == blocks


def test_coercion_is_idempotent():
    once = coerce_input("hi")
    assert coerce_input(once) == once  # the double-wrap guard: no [[TextBlock]]


def test_empty_list_is_valid_input():
    assert coerce_input([]) == []


@pytest.mark.parametrize("value", [None, 42, {"type": "text", "text": "hi"}, [{"type": "text", "text": "hi"}]])
def test_anything_else_raises(value):
    with pytest.raises(TypeError):
        coerce_input(value)
