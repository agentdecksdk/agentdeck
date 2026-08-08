"""Typed content blocks.

``Input`` is what every boundary passes instead of a bare string, in both directions —
``run.completed`` carries one too.

Blocks are stored in full: they are the caller's own input and the run's declared result, and a
truncated one cannot be replayed. Only *tool* results are bounded (preview + size + hash), where
the bytes are engine-chosen rather than caller-chosen.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, get_args

from pydantic import (
    ConfigDict,
    Field,
    ValidationError,
    ValidatorFunctionWrapHandler,
    WrapValidator,
    field_validator,
)

from agentdeck.core.base import CoreModel, JsonData


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

    ``JsonData`` is the type, so a value that could not survive the wire is rejected at
    construction instead of failing later in a store or a sink. Prose belongs in a ``TextBlock``,
    which is what readers render.
    """

    type: Literal["data"] = "data"
    data: JsonData


class UnknownBlock(CoreModel):
    """A block ``type`` this version doesn't know: consumers skip it, stores keep it.

    Strict on purpose — it sits in a union with the known blocks, so anything laxer would
    let a malformed known block validate here instead of raising.
    """

    model_config = ConfigDict(extra="forbid")

    type: str
    raw_block: dict[str, JsonData]

    @field_validator("type")
    @classmethod
    def _is_not_a_known_type(cls, value: str) -> str:
        if value in KNOWN_BLOCK_TYPES:
            raise ValueError(f"{value!r} is a known block type — use its block class")
        return value


KnownBlock = Annotated[TextBlock | ImageBlock | ResourceBlock | DataBlock, Field(discriminator="type")]

# Both derived by peeling the Annotated, then the union: a block class added above reaches the
# fallback and ``coerce_input`` without anyone remembering to list it twice more.
KNOWN_BLOCK_TYPES: frozenset[str] = frozenset(b.model_fields["type"].default for b in get_args(get_args(KnownBlock)[0]))


def _fallback_to_unknown_block(value: Any, handler: ValidatorFunctionWrapHandler) -> Any:
    """Reshape an unfamiliar block into :class:`UnknownBlock` instead of failing the union.

    A stored ``UnknownBlock`` (``{type, raw_block}``) validates against the union member directly,
    so ``handler`` succeeds and it is never re-wrapped — which is what lets it round-trip.
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

_BLOCK_TYPES = (*get_args(get_args(KnownBlock)[0]), UnknownBlock)


def coerce_input(value: str | Input) -> Input:
    """A string becomes one ``TextBlock``; an ``Input`` passes through, so calling twice is
    safe. Anything else raises."""
    if isinstance(value, str):
        return [TextBlock(text=value)]
    if isinstance(value, list) and all(isinstance(block, _BLOCK_TYPES) for block in value):
        return list(value)
    raise TypeError(f"expected str or list[ContentBlock], got {type(value).__name__}")
