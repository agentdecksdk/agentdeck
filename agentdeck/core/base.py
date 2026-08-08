"""What every core model is built on: one base class, one JSON type.

Both are shared by modules with nothing else in common — content blocks, event payloads,
invocable specs, tool sets — so neither lives in whichever one needed it first.
"""

from __future__ import annotations

from math import isfinite
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, JsonValue


class CoreModel(BaseModel):
    """Base for the schema models: unknown fields are dropped, and nothing mutates.

    Dropping them is forward compatibility — a field a newer writer added has to land, not
    raise. A model that is built rather than parsed sets ``extra="forbid"`` instead.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)


def _reject_non_finite(value: JsonValue) -> JsonValue:
    """Iterative, not recursive: anything deep enough to recurse is already rejected by
    ``JsonValue``, and this must not turn that into a ``RecursionError``."""
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


JsonData = Annotated[JsonValue, AfterValidator(_reject_non_finite)]
"""JSON a store can hold and hand back unchanged.

Two halves of one rule, because neither is native alone: ``JsonValue`` refuses what JSON has no
shape for (a set, a datetime) but accepts ``NaN``/``±Infinity``, which serialize to ``null``.
``Field(allow_inf_nan=False)`` does not fix that — on a union it applies to the whole value and
raises ``TypeError`` on the first dict; hand-writing the recursive alias to constrain its float
member instead lets a ``set`` coerce to a ``list``, trading one silent divergence for another."""
