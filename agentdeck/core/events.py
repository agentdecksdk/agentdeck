"""Event schema v1: one envelope, one payload per kind.

Engines produce payloads, the Runtime fills the envelope — so an engine cannot get
``seq`` or ``tenant`` wrong. ``seq`` is per-run and contiguous from 0, which makes it
both the ordering authority and a loss check; ``ts`` is informational.

Unknown kinds parse instead of raising (:func:`parse_event`) and unknown fields inside a
known payload are dropped, so an old reader survives a newer writer. Adding a kind or an
optional field stays compatible; renaming or removing one means bumping ``v``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Literal, get_args

from pydantic import AwareDatetime, ConfigDict, Field, NonNegativeInt, ValidationError, field_validator, model_validator

from agentdeck.core.content import CoreModel, Input

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

RESULT_PREVIEW_MAX = 4096

TERMINAL_KINDS = frozenset({"run.completed", "run.failed", "run.cancelled"})


class Usage(CoreModel):
    """Token and cost accounting. ``usd`` is None when no price is known for the model."""

    input_tokens: NonNegativeInt
    output_tokens: NonNegativeInt
    usd: float | None = None


class Budget(CoreModel):
    """The caps the run was admitted under; None means uncapped on that axis."""

    max_usd: float | None = None
    max_tokens: int | None = None


class RunContextSnapshot(CoreModel):
    """Enough of the run's context to reconstruct it from the log alone."""

    principal: str
    trace_id: str
    budget: Budget | None = None
    triggered_by: str | None = None


class RunStarted(CoreModel):
    """Opens a run and carries its per-run constants."""

    kind: Literal["run.started"] = "run.started"
    invocable: str
    kind_of_invocable: Literal["agent", "workflow", "skill"]
    parent_run_id: str | None = None
    input: Input
    context: RunContextSnapshot


class RunCompleted(CoreModel):
    """Terminal. ``usage`` is the authoritative total for the run."""

    kind: Literal["run.completed"] = "run.completed"
    output: Input
    usage: Usage


class RunFailed(CoreModel):
    """Terminal. ``error_code`` is closed so callers branch on it instead of parsing prose."""

    kind: Literal["run.failed"] = "run.failed"
    error_code: Literal["engine_error", "tool_error", "budget_exceeded", "deadline", "cancelled_hard"]
    message: str
    retryable: bool


class RunPaused(CoreModel):
    """Cooperative pause — not terminal, and not waiting on an answer."""

    kind: Literal["run.paused"] = "run.paused"
    reason: str | None = None


class RunResumed(CoreModel):
    """The run continues: same ``run_id``, ``seq`` keeps counting."""

    kind: Literal["run.resumed"] = "run.resumed"
    reason: str | None = None


class RunCancelled(CoreModel):
    """Terminal."""

    kind: Literal["run.cancelled"] = "run.cancelled"
    reason: str | None = None


class RunInterrupted(CoreModel):
    """Waiting on an answer. Not terminal — ``run_id`` and ``seq`` continue on resume."""

    kind: Literal["run.interrupted"] = "run.interrupted"
    interrupt_id: str
    reason: Literal["human", "pause", "approval"]
    payload: dict[str, Any]
    thread_id: str | None = None
    expected_resume: str | None = None


class TextDelta(CoreModel):
    """One streamed fragment; ``message.completed`` is the record."""

    kind: Literal["text.delta"] = "text.delta"
    message_id: str
    text: str


class ThoughtDelta(CoreModel):
    """Reasoning fragment — same framing as ``text.delta``, separate channel."""

    kind: Literal["thought.delta"] = "thought.delta"
    message_id: str
    text: str


class MessageCompleted(CoreModel):
    """The full final text — deltas are streaming UX. One ``message_id`` never spans two origins."""

    kind: Literal["message.completed"] = "message.completed"
    message_id: str
    text: str


class ToolCallStarted(CoreModel):
    """Dispatch; paired with ``tool.call.completed`` by ``call_id``."""

    kind: Literal["tool.call.started"] = "tool.call.started"
    call_id: str
    tool: str
    args: dict[str, Any]


class ToolCallCompleted(CoreModel):
    """A capped preview plus size and hash, never the result itself."""

    kind: Literal["tool.call.completed"] = "tool.call.completed"
    call_id: str
    tool: str
    result_preview: str
    result_size: NonNegativeInt
    result_sha256: str
    artifact_id: str | None = None
    error: str | None = None

    @field_validator("result_preview")
    @classmethod
    def _preview_within_cap(cls, value: str) -> str:
        if len(value) > RESULT_PREVIEW_MAX:
            raise ValueError(f"result_preview is {len(value)} chars, over RESULT_PREVIEW_MAX={RESULT_PREVIEW_MAX}")
        return value


class NodeUpdated(CoreModel):
    """``state_patch`` shallow-merges into the state: top-level keys replace."""

    kind: Literal["node.updated"] = "node.updated"
    node: str
    state_patch: dict[str, Any]


class ArtifactCreated(CoreModel):
    """A reference to bytes stored elsewhere."""

    kind: Literal["artifact.created"] = "artifact.created"
    artifact_id: str
    media_type: str
    uri: str
    size: NonNegativeInt


class UsageReported(CoreModel):
    """One model call, advisory — the total on ``run.completed`` wins."""

    kind: Literal["usage.reported"] = "usage.reported"
    model: str
    usage: Usage


class InputAppended(CoreModel):
    """Mid-turn steering. No producer yet."""

    kind: Literal["input.appended"] = "input.appended"
    input: Input
    source: str


class Custom(CoreModel):
    """Engine-specific event; ``name`` must be namespaced."""

    kind: Literal["custom"] = "custom"
    name: str
    data: dict[str, Any]

    @field_validator("name")
    @classmethod
    def _name_is_namespaced(cls, value: str) -> str:
        namespace, _, event = value.partition(".")
        if not namespace or not event:
            raise ValueError(f"custom name must be '<namespace>.<event>', got {value!r}")
        return value


class UnknownEvent(CoreModel):
    """A kind this version doesn't know: consumers skip it, stores keep it.

    Strict on purpose — it sits in a union with the known payloads, so anything laxer
    would let a malformed known payload validate here instead of raising.
    """

    model_config = ConfigDict(extra="forbid")

    kind: str
    raw_payload: dict[str, Any]

    @field_validator("kind")
    @classmethod
    def _is_not_a_known_kind(cls, value: str) -> str:
        if value in KNOWN_KINDS:
            raise ValueError(f"{value!r} is a known kind — use its payload class")
        return value


KnownPayload = Annotated[
    RunStarted
    | RunCompleted
    | RunFailed
    | RunPaused
    | RunResumed
    | RunCancelled
    | RunInterrupted
    | TextDelta
    | ThoughtDelta
    | MessageCompleted
    | ToolCallStarted
    | ToolCallCompleted
    | NodeUpdated
    | ArtifactCreated
    | UsageReported
    | InputAppended
    | Custom,
    Field(discriminator="kind"),
]

# peels the Annotated, then the union — add a payload class above and this follows it
KNOWN_KINDS: frozenset[str] = frozenset(p.model_fields["kind"].default for p in get_args(get_args(KnownPayload)[0]))


class Event(CoreModel):
    """The eight fields every event carries, whatever its kind.

    Per-run constants belong in the ``run.started`` payload, not here. Unknown envelope
    fields are ignored rather than rejected, so a newer writer can't break this reader.
    """

    v: int = 1
    kind: str
    seq: NonNegativeInt
    run_id: str
    session_id: str | None
    tenant: str
    origin: str
    ts: AwareDatetime
    payload: KnownPayload | UnknownEvent

    @model_validator(mode="after")
    def _kind_mirrors_payload(self) -> Event:
        if self.kind != self.payload.kind:
            raise ValueError(f"envelope kind {self.kind!r} does not match payload kind {self.payload.kind!r}")
        return self


def parse_event(data: dict[str, Any]) -> Event:
    """Parse one event: an unknown ``kind`` yields an :class:`UnknownEvent` payload with
    the envelope still validated. A malformed *known* payload raises.

    An unknown payload of exactly ``{kind, raw_payload}`` is read as already wrapped —
    that ambiguity is what lets a stored ``UnknownEvent`` round-trip.
    """
    try:
        return Event.model_validate(data)
    except ValidationError:
        kind, payload = data.get("kind"), data.get("payload")
        if not isinstance(kind, str) or kind in KNOWN_KINDS or not isinstance(payload, dict):
            raise
        return Event.model_validate({**data, "payload": {"kind": kind, "raw_payload": payload}})


def check_contiguous(events: Iterable[Event]) -> list[int]:
    """Missing ``seq`` numbers for one run — gaps only, duplicates aren't checked."""
    run = list(events)
    if len({event.run_id for event in run}) > 1:
        raise ValueError("check_contiguous takes one run's events")
    seqs = {event.seq for event in run}
    if not seqs:
        return []
    return [n for n in range(max(seqs) + 1) if n not in seqs]


def check_terminal(events: Sequence[Event]) -> str | None:
    """``None`` if exactly one terminal event closes the run, else what's wrong."""
    at = [i for i, event in enumerate(events) if event.kind in TERMINAL_KINDS]
    if not at:
        return "no terminal event"
    if len(at) > 1:
        return f"{len(at)} terminal events: {[events[i].kind for i in at]}"
    if at[0] != len(events) - 1:
        return f"terminal event {events[at[0]].kind!r} at index {at[0]} of {len(events)}, not last"
    return None


__all__ = [
    "KNOWN_KINDS",
    "RESULT_PREVIEW_MAX",
    "TERMINAL_KINDS",
    "ArtifactCreated",
    "Budget",
    "Custom",
    "Event",
    "InputAppended",
    "KnownPayload",
    "MessageCompleted",
    "NodeUpdated",
    "RunCancelled",
    "RunCompleted",
    "RunContextSnapshot",
    "RunFailed",
    "RunInterrupted",
    "RunPaused",
    "RunResumed",
    "RunStarted",
    "TextDelta",
    "ThoughtDelta",
    "ToolCallCompleted",
    "ToolCallStarted",
    "UnknownEvent",
    "Usage",
    "UsageReported",
    "check_contiguous",
    "check_terminal",
    "parse_event",
]
