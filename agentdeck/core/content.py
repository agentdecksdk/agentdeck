"""Typed content blocks.

``Input`` is what every boundary passes instead of a bare string: a list of blocks
discriminated on ``type``, so text, images, references and structured data travel the same
way — in both directions, since ``run.completed`` carries an ``Input`` too.

Content policy: text and data blocks are stored in full, because they are the caller's own
input and the run's own declared result — a truncated one cannot be replayed or reconciled
against engine state. Only *tool* results are bounded (preview + size + hash), where the
bytes are unbounded and engine-chosen rather than caller-chosen.

An unfamiliar block ``type`` falls back to :class:`UnknownBlock` instead of rejecting the
block's whole event, mirroring how :func:`agentdeck.core.events.parse_event` treats an
unknown ``kind``.
"""

from __future__ import annotations

from math import isfinite
from typing import Annotated, Any, Literal, get_args

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    ValidatorFunctionWrapHandler,
    WrapValidator,
    field_validator,
)


class CoreModel(BaseModel):
    """Base for the schema models: unknown fields are dropped, and nothing mutates."""

    model_config = ConfigDict(extra="ignore", frozen=True)


class TextBlock(CoreModel):
    type: Literal["text"] = "text"
    text: str


class ImageBlock(CoreModel):
    type: Literal["image"] = "image"
    media_type: str
    data_b64: str


class ResourceBlock(CoreModel):
    """Bytes held elsewhere, referenced by uri."""

    type: Literal["resource"] = "resource"
    uri: str
    media_type: str | None = None


class DataBlock(CoreModel):
    """JSON data as content: a validated ``output_type`` result, a workflow's state.

    ``JsonValue`` is the type, so a value that could not survive the wire is rejected at
    construction instead of failing later in a store or a sink. Prose belongs in a ``TextBlock``,
    which is what readers render.
    """

    type: Literal["data"] = "data"
    data: JsonValue

    @field_validator("data")
    @classmethod
    def _floats_are_finite(cls, value: JsonValue) -> JsonValue:
        """``NaN`` and ``±Infinity`` pass ``JsonValue``'s float branch but have no JSON literal:
        they serialize as ``null``, so a consumer would see a number the store does not hold — the
        one silent divergence between a yielded event and its record.

        Iterative, not recursive: a payload deep enough to recurse is already rejected by
        ``JsonValue``, and this must not turn that into a ``RecursionError``.
        """
        stack: list[JsonValue] = [value]
        while stack:
            item = stack.pop()
            if isinstance(item, float) and not isfinite(item):
                raise ValueError(f"data holds a non-finite float ({item}), which JSON cannot carry")
            if isinstance(item, dict):
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)
        return value


class UnknownBlock(CoreModel):
    """A block ``type`` this version doesn't know: consumers skip it, stores keep it.

    Strict on purpose — it sits in a union with the known blocks, so anything laxer would
    let a malformed known block validate here instead of raising.
    """

    model_config = ConfigDict(extra="forbid")

    type: str
    raw_block: dict[str, Any]

    @field_validator("type")
    @classmethod
    def _is_not_a_known_type(cls, value: str) -> str:
        if value in KNOWN_BLOCK_TYPES:
            raise ValueError(f"{value!r} is a known block type — use its block class")
        return value


KnownBlock = Annotated[TextBlock | ImageBlock | ResourceBlock | DataBlock, Field(discriminator="type")]

# peels the Annotated, then the union — add a block class above and this follows it
KNOWN_BLOCK_TYPES: frozenset[str] = frozenset(b.model_fields["type"].default for b in get_args(get_args(KnownBlock)[0]))


def _fallback_to_unknown_block(value: Any, handler: ValidatorFunctionWrapHandler) -> Any:
    """Reshape an unfamiliar block into :class:`UnknownBlock` instead of failing the union.

    A dict already shaped like a stored ``UnknownBlock`` (``{type, raw_block}``) validates against
    the union member directly, so ``handler`` succeeds and this never re-wraps it — which is what
    lets a stored ``UnknownBlock`` round-trip.
    """
    try:
        return handler(value)
    except ValidationError:
        if isinstance(value, dict):
            block_type = value.get("type")
            if isinstance(block_type, str) and block_type not in KNOWN_BLOCK_TYPES:
                return UnknownBlock(type=block_type, raw_block=value)
        raise


ContentBlock = Annotated[KnownBlock | UnknownBlock, WrapValidator(_fallback_to_unknown_block)]
Input = list[ContentBlock]

# derived from the union for the same reason KNOWN_BLOCK_TYPES is: a block class added above
# has to reach coerce_input's isinstance check without anyone remembering to list it twice
_BLOCK_TYPES = (*get_args(get_args(KnownBlock)[0]), UnknownBlock)


def coerce_input(value: str | Input) -> Input:
    """A string becomes one ``TextBlock``; an ``Input`` passes through, so calling twice is
    safe. Anything else raises."""
    if isinstance(value, str):
        return [TextBlock(text=value)]
    if isinstance(value, list) and all(isinstance(block, _BLOCK_TYPES) for block in value):
        return list(value)
    raise TypeError(f"expected str or list[ContentBlock], got {type(value).__name__}")


__all__ = [
    "KNOWN_BLOCK_TYPES",
    "ContentBlock",
    "CoreModel",
    "DataBlock",
    "ImageBlock",
    "Input",
    "KnownBlock",
    "ResourceBlock",
    "TextBlock",
    "UnknownBlock",
    "coerce_input",
]
