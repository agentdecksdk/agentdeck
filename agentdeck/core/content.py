"""Typed content blocks.

``Input`` is what every boundary passes instead of a bare string: a list of blocks
discriminated on ``type``, so text, images, references and structured data travel the same
way — in both directions, since ``run.completed`` carries an ``Input`` too.

Content policy: text and data blocks are stored in full, because they are the caller's own
input and the run's own declared result — a truncated one cannot be replayed or reconciled
against engine state. Only *tool* results are bounded (preview + size + hash), where the
bytes are unbounded and engine-chosen rather than caller-chosen.
"""

from __future__ import annotations

from math import isfinite
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator


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

    ``JsonValue`` is the type, so a value that could not survive the wire is rejected here
    at construction instead of failing later in a store or a sink. An object, an array or a
    scalar all fit — but prose belongs in a ``TextBlock``, which is what readers render.
    """

    type: Literal["data"] = "data"
    data: JsonValue

    @field_validator("data")
    @classmethod
    def _floats_are_finite(cls, value: JsonValue) -> JsonValue:
        """``NaN`` and ``±Infinity`` pass ``JsonValue``'s float branch but have no JSON
        literal: they serialize as ``null``, so a consumer would see a number that the store
        does not hold — the one silent divergence between a yielded event and its record.
        A producer meaning "no value" says so with ``null``; one meaning "not a number" says
        so with a string.

        The walk is iterative: a payload deep enough to recurse is already rejected by
        ``JsonValue`` itself, and this must not be the thing that turns that into a
        ``RecursionError``.
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


ContentBlock = Annotated[TextBlock | ImageBlock | ResourceBlock | DataBlock, Field(discriminator="type")]
Input = list[ContentBlock]

_BLOCK_TYPES = (TextBlock, ImageBlock, ResourceBlock, DataBlock)


def coerce_input(value: str | Input) -> Input:
    """A string becomes one ``TextBlock``; an ``Input`` passes through, so calling twice is
    safe. Anything else raises."""
    if isinstance(value, str):
        return [TextBlock(text=value)]
    if isinstance(value, list) and all(isinstance(block, _BLOCK_TYPES) for block in value):
        return list(value)
    raise TypeError(f"expected str or list[ContentBlock], got {type(value).__name__}")


__all__ = [
    "ContentBlock",
    "CoreModel",
    "DataBlock",
    "ImageBlock",
    "Input",
    "ResourceBlock",
    "TextBlock",
    "coerce_input",
]
