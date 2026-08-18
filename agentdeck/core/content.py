"""Typed content blocks.

``Input`` is what every boundary passes instead of a bare string, in both directions  -
``run.completed`` carries one too.

Blocks are stored in full: they are the caller's own input and the run's declared result, and a
truncated one cannot be replayed. Only *tool* results are bounded (preview + size + hash), where
the bytes are engine-chosen rather than caller-chosen.
"""

from __future__ import annotations

import base64
from typing import Annotated, Any, Literal, get_args

from pydantic import (
    ConfigDict,
    Field,
    ValidationError,
    ValidatorFunctionWrapHandler,
    WrapValidator,
    field_validator,
    model_serializer,
)

from agentdeck.core.base import CoreModel, JsonData

INLINE_BYTES_CAP = 1024 * 1024
"""1 MB decoded, enforced on every inline block (:class:`ImageBlock`, :class:`AudioBlock`).

Base64 in an event lands in an append-only log and replays down every SSE connection for the
life of that run, so a documented-only limit is a limit that ships violated. Deliberately low:
raising the cap later is compatible, lowering it is not."""


def _capped_inline(value: str) -> str:
    """Reject inline base64 over :data:`INLINE_BYTES_CAP` decoded bytes.

    ``b64decode`` is called exactly once  -  the decoded length it returns is also the
    measurement, so nothing here decodes the payload a second time just to size it.
    """
    decoded_size = len(base64.b64decode(value))
    if decoded_size > INLINE_BYTES_CAP:
        raise ValueError(
            f"inline data is {decoded_size} decoded bytes, over the {INLINE_BYTES_CAP}-byte cap  -  "
            "use ResourceBlock for anything larger"
        )
    return value


class TextBlock(CoreModel):
    type: Literal["text"] = "text"
    text: str


class ImageBlock(CoreModel):
    type: Literal["image"] = "image"
    media_type: str
    data_b64: str

    @field_validator("data_b64")
    @classmethod
    def _cap_inline(cls, value: str) -> str:
        return _capped_inline(value)


class AudioBlock(CoreModel):
    """Audio bytes inline: a voice note, a recorded call. Held elsewhere -> ``ResourceBlock``."""

    type: Literal["audio"] = "audio"
    media_type: str
    data_b64: str

    @field_validator("data_b64")
    @classmethod
    def _cap_inline(cls, value: str) -> str:
        return _capped_inline(value)


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

    Strict on purpose  -  it sits in a union with the known blocks, so anything laxer would
    let a malformed known block validate here instead of raising.
    """

    model_config = ConfigDict(extra="forbid")

    type: str
    raw_block: dict[str, JsonData]

    @field_validator("type")
    @classmethod
    def _is_not_a_known_type(cls, value: str) -> str:
        if value in KNOWN_BLOCK_TYPES:
            raise ValueError(f"{value!r} is a known block type  -  use its block class")
        return value

    @model_serializer
    def _dump_raw_block(self) -> dict[str, JsonData]:
        """Dump ``raw_block`` verbatim instead of nesting it under ``{type, raw_block}``: the
        wrapping is a parse-time artifact, so a reader that re-emits an unknown block must see
        the same dict it read, not one nested one level deeper."""
        return self.raw_block


KnownBlock = Annotated[TextBlock | ImageBlock | AudioBlock | ResourceBlock | DataBlock, Field(discriminator="type")]

# Both derived by peeling the Annotated, then the union: a block class added above reaches the
# fallback and ``coerce_input`` without anyone remembering to list it twice more.
KNOWN_BLOCK_TYPES: frozenset[str] = frozenset(b.model_fields["type"].default for b in get_args(get_args(KnownBlock)[0]))


def _fallback_to_unknown_block(value: Any, handler: ValidatorFunctionWrapHandler) -> Any:
    """Reshape an unfamiliar block into :class:`UnknownBlock` instead of failing the union.

    ``UnknownBlock`` dumps as its own ``raw_block`` verbatim (its ``model_serializer``), so a
    second parse meets the original dict again rather than ``{type, raw_block}``  -  ``handler``
    fails on it exactly as it did the first time, and this re-wraps it into an equal
    ``UnknownBlock``. The serializer is what makes parse-then-dump the identity; this function
    runs on every parse, not only the first.
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
