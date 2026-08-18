"""State helpers for workflow runners and tool wrappers."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping
from typing import Any

from langgraph.types import Command
from pydantic import BaseModel


def coerce_input(value: Any, schema: type) -> Any:
    """Normalise a workflow caller's ``value`` into a graph-input dict."""
    if value is None:
        return {}
    if isinstance(value, Command):
        return value  # resume directive, not state  -  LangGraph consumes it as-is
    if isinstance(value, schema):
        return value.model_dump() if isinstance(value, BaseModel) else value
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        return {"input": value}
    raise TypeError(f"workflow input must be a {schema.__name__} instance, mapping, or str; got {type(value).__name__}")


def json_default(value: Any) -> Any:
    """JSON encoder fallback for Pydantic / dataclass / arbitrary objects."""
    if isinstance(value, BaseModel):
        return value.model_dump()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    if hasattr(value, "__dict__"):
        return value.__dict__
    return str(value)


def dump_state(state: Any) -> str:
    return json.dumps(state, default=json_default, ensure_ascii=False)


__all__ = ["coerce_input", "dump_state", "json_default"]
