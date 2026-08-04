"""Typed content blocks.

``Input`` is what every boundary passes instead of a bare string: a list of blocks
discriminated on ``type``, so text, images and references travel the same way.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


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


ContentBlock = Annotated[TextBlock | ImageBlock | ResourceBlock, Field(discriminator="type")]
Input = list[ContentBlock]

_BLOCK_TYPES = (TextBlock, ImageBlock, ResourceBlock)


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
    "ImageBlock",
    "Input",
    "ResourceBlock",
    "TextBlock",
    "coerce_input",
]
