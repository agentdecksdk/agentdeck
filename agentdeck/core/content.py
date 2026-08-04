"""Content blocks — the one input/output shape core knows about.

``Input`` replaces the ad-hoc ``message: str`` / ``input: Any`` shapes at every
boundary: a list of typed blocks discriminated on ``type``, so text, images and
out-of-band resources travel the same channel.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class CoreModel(BaseModel):
    """Base for every core schema model: unknown fields are dropped, never fatal (D8)."""

    model_config = ConfigDict(extra="ignore")


class TextBlock(CoreModel):
    type: Literal["text"] = "text"
    text: str


class ImageBlock(CoreModel):
    type: Literal["image"] = "image"
    media_type: str
    data_b64: str


class ResourceBlock(CoreModel):
    """A payload held elsewhere — bytes never travel inline."""

    type: Literal["resource"] = "resource"
    uri: str
    media_type: str | None = None


ContentBlock = Annotated[TextBlock | ImageBlock | ResourceBlock, Field(discriminator="type")]
Input = list[ContentBlock]

_BLOCK_TYPES = (TextBlock, ImageBlock, ResourceBlock)


def coerce_input(value: str | Input) -> Input:
    """A bare string becomes one ``TextBlock``, an ``Input`` passes through unchanged
    (so the call is idempotent), anything else raises rather than being guessed at."""
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
