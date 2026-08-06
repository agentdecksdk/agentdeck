"""``coerce_input`` — the one place a bare string becomes an ``Input`` — and ``DataBlock``,
the one place structured data becomes content."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from agentdeck.core import ContentBlock, DataBlock, ImageBlock, ResourceBlock, TextBlock, UnknownBlock, coerce_input

BLOCKS = TypeAdapter(list[ContentBlock])


def test_string_becomes_one_text_block():
    assert coerce_input("hi") == [TextBlock(text="hi")]


def test_input_passes_through_unchanged():
    blocks = [
        TextBlock(text="hi"),
        ImageBlock(media_type="image/png", data_b64="AA=="),
        ResourceBlock(uri="s3://b/k"),
        DataBlock(data={"claim": 7777}),
    ]
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


# --- DataBlock -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "data",
    [
        {"claim_id": "7777", "decision": "approved", "amount": 12.5},
        {"nested": {"deep": [1, 2, {"deeper": None}]}},
        [{"id": 1}, {"id": 2}],
        "already a string",
        42,
        True,
        None,
    ],
)
def test_a_data_block_round_trips_every_json_shape(data):
    block = DataBlock(data=data)
    assert BLOCKS.validate_json(BLOCKS.dump_json([block])) == [block]


def test_the_discriminator_routes_data_to_a_data_block():
    assert BLOCKS.validate_python([{"type": "data", "data": {"k": 1}}]) == [DataBlock(data={"k": 1})]


def test_an_unknown_field_inside_a_data_block_is_dropped():
    assert BLOCKS.validate_python([{"type": "data", "data": {"k": 1}, "encoding": "cbor"}]) == [
        DataBlock(data={"k": 1})
    ]


def test_data_is_stored_in_full_never_previewed_or_hashed():
    """The content policy: caller input and a run's declared result survive whole, so a
    replay sees what the run saw. Only tool results are bounded."""
    big = {"rows": [{"i": i, "note": "x" * 100} for i in range(500)]}
    block = DataBlock(data=big)
    assert block.data == big
    assert BLOCKS.validate_json(BLOCKS.dump_json([block]))[0].data == big
    assert set(DataBlock.model_fields) == {"type", "data"}  # no preview, no size, no sha256


@pytest.mark.parametrize("data", [{"when": datetime(2026, 1, 1, tzinfo=UTC)}, {"tags": {"a", "b"}}, object()])
def test_a_value_that_could_not_survive_the_wire_is_rejected(data):
    with pytest.raises(ValidationError):
        DataBlock(data=data)


@pytest.mark.parametrize(
    "data",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        {"ratio": float("nan")},
        [1.0, float("inf")],
        {"deep": [{"ratio": float("nan")}]},
    ],
)
def test_a_non_finite_float_is_rejected_rather_than_serialized_as_null(data):
    """The one value JSON has no literal for. Accepting it would write ``null`` while the
    consumer that was handed the block saw a float — a yielded event diverging from its own
    record, silently."""
    with pytest.raises(ValidationError, match="non-finite float"):
        DataBlock(data=data)


def test_finite_floats_at_the_edges_are_still_fine():
    for value in (0.0, -0.0, 1e308, -1e308, 5e-324):
        assert DataBlock(data={"v": value}).data == {"v": value}


def test_a_data_block_does_not_mutate():
    block = DataBlock(data={"k": 1})
    with pytest.raises(ValidationError):
        block.data = {"k": 2}


# --- UnknownBlock (#109) ----------------------------------------------------------------


def test_an_unfamiliar_block_type_parses_as_unknown_block():
    raw = {"type": "audio", "uri": "s3://clip.mp3", "duration_s": 12}
    assert BLOCKS.validate_python([raw]) == [UnknownBlock(type="audio", raw_block=raw)]


def test_a_malformed_known_block_still_raises():
    """The union must not swallow a broken known block into UnknownBlock — only a type it
    genuinely doesn't recognize falls back."""
    with pytest.raises(ValidationError):
        BLOCKS.validate_python([{"type": "text"}])  # text requires `text`


def test_unknown_block_survives_its_own_round_trip():
    once = BLOCKS.validate_python([{"type": "audio", "uri": "s3://clip.mp3"}])
    assert BLOCKS.validate_json(BLOCKS.dump_json(once)) == once  # no double-wrapping


def test_a_block_named_raw_block_does_not_slip_past_its_own_schema():
    """The UnknownBlock arm must not become a bypass for a malformed known block: a known
    type with a `raw_block` field must still raise, not validate as UnknownBlock."""
    with pytest.raises(ValidationError):
        BLOCKS.validate_python([{"type": "text", "raw_block": {"a": 1}}])


def test_unknown_block_refuses_a_known_type():
    with pytest.raises(ValidationError, match="known block type"):
        UnknownBlock(type="text", raw_block={})


def test_a_consumer_skips_unknown_blocks_and_renders_the_rest():
    blocks = BLOCKS.validate_python(
        [
            {"type": "text", "text": "a"},
            {"type": "audio", "uri": "s3://clip.mp3"},
            {"type": "text", "text": "b"},
        ]
    )
    assert "".join(b.text for b in blocks if isinstance(b, TextBlock)) == "ab"
    assert sum(isinstance(b, UnknownBlock) for b in blocks) == 1


def test_coerce_input_passes_through_an_unknown_block():
    blocks = [TextBlock(text="hi"), UnknownBlock(type="audio", raw_block={"type": "audio"})]
    assert coerce_input(blocks) == blocks
