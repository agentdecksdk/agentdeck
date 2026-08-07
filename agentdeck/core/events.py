"""Event schema v1: one envelope, one payload per kind.

Engines produce payloads, the Runtime fills the envelope — so an engine cannot get
``seq`` or ``tenant`` wrong. ``seq`` is per-run and contiguous from 0, which makes it
both the ordering authority and a loss check; ``ts`` is informational.

Unknown kinds parse instead of raising (:func:`parse_event`) and unknown fields inside a
known payload are dropped, so an old reader survives a newer writer. Adding a kind or an
optional field stays compatible; renaming or removing one means bumping ``v``.

Run control is three phases rather than one event: ``control.requested`` records that a
signal was written, ``control.observed`` records that the run reached a safe point and acted
on it, and the verb's own kind records the effect (``run.cancelled``, ``run.paused``,
``run.resumed``, ``input.appended``). One pair of kinds carries every verb, so pause and
steering need no vocabulary of their own — and a caller can finally tell "the signal is
recorded" from "the run actually stopped", which is the whole difference between cooperative
control and control.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Literal, get_args

from pydantic import (
    AwareDatetime,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveInt,
    ValidationError,
    field_validator,
    model_validator,
)

from agentdeck.core.content import CoreModel, Input

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

RESULT_PREVIEW_MAX = 4096

TERMINAL_KINDS = frozenset({"run.completed", "run.failed", "run.cancelled"})

Money = Annotated[float, Field(ge=0, allow_inf_nan=False)]
"""US dollars. Constrained where the token counts already were, plus one reason of its own:
``NaN``/``±Infinity`` have no JSON literal, so they serialize as ``null`` and a consumer reads
*no cost* where the producer wrote nonsense — the divergence ``DataBlock`` rejects for arbitrary
data, and money is the last field that should be exempt from it."""


class Usage(CoreModel):
    """Token and cost accounting. ``usd`` is None when no price is known for the model."""

    input_tokens: NonNegativeInt
    output_tokens: NonNegativeInt
    usd: Money | None = None


class Budget(CoreModel):
    """The caps the run was admitted under; None means uncapped on that axis."""

    max_usd: Money | None = None
    max_tokens: NonNegativeInt | None = None


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
    """Terminal. ``usage`` is the authoritative total for the run.

    A structured result — a validated ``output_type``, a workflow's final state — is a
    ``DataBlock`` in ``output``, not a namespaced ``custom`` event and not a stringified dict.
    """

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
    """The run continues: same ``run_id``, ``seq`` keeps counting.

    ``value`` is the answer this resume carries, stored in full like ``run.started.input``: it is
    the caller's own input and a truncated one cannot be replayed. It rides on this event so that
    the write flipping ``WAITING_HUMAN`` to ``RUNNING`` is the same write that stores the answer —
    two writes leave a window where the log says a run was answered but no longer holds what the
    answer was. ``None`` is a resume answering nothing, which is what lifting a pause looks like.

    Not irreversible: the log is append-only and status is a fold over it, so a resume that cannot
    be carried through returns the run to ``WAITING_HUMAN`` by recording its interrupt again.
    """

    kind: Literal["run.resumed"] = "run.resumed"
    reason: str | None = None
    value: Input | None = None


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


ControlVerb = Literal["cancel", "pause", "resume", "steer"]
"""Every verb run control has, including the ones whose behavior isn't built yet.

Complete at birth deliberately: a member added later is not additive for a *reader*, which
rejects the whole event rather than skipping it the way it can an unknown kind.
"""

SafePoint = Literal["stream_item", "tool_dispatch", "node_boundary"]
"""Where a run can notice a signal: between two streamed items, before a tool is dispatched,
or at a graph node boundary. Closed for the same reason ``ControlVerb`` is."""


class ControlRequested(CoreModel):
    """A control signal was recorded for this run — not that the run has acted on it.

    At the moment a caller asks, only this is knowable: the run may be inside a tool call that has
    to return first. So a request is never a status transition — a run stays ``RUNNING`` until
    ``run.paused`` or ``run.cancelled`` says otherwise. A signal that lost the race with a
    terminal event records nothing: a terminal event is a run's last by invariant.
    """

    kind: Literal["control.requested"] = "control.requested"
    verb: ControlVerb
    reason: str | None = None


class ControlObserved(CoreModel):
    """The run reached a safe point, found the signal, and is acting on it.

    ``safe_point`` names where, because "cancel took eight seconds" and "cancel took eight seconds
    because a tool call did" are different answers to the same complaint. The verb's own kind
    records the effect: this event says the run noticed, not that it stopped.
    """

    kind: Literal["control.observed"] = "control.observed"
    verb: ControlVerb
    safe_point: SafePoint


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


class StatusReported(CoreModel):
    """Advisory: what the run is doing right now, in words a person can read.

    Not a transition. Status is folded from the lifecycle kinds (``core/status.py``), so a run
    reporting ``"Searching GitHub"`` is still ``RUNNING`` and a log full of these folds to exactly
    what a log with none of them does.
    """

    kind: Literal["status.reported"] = "status.reported"
    message: str = Field(min_length=1)


class ProgressReported(CoreModel):
    """Advisory: which named stage the run is on, optionally counted.

    ``step`` is required; the counts are not, because a run that knows its stage often does not
    know how many there are. Never a percentage — a consumer that wants one divides, and only
    when ``total`` is present.
    """

    kind: Literal["progress.reported"] = "progress.reported"
    step: str = Field(min_length=1)
    current: NonNegativeInt | None = None
    total: PositiveInt | None = None

    @model_validator(mode="after")
    def _current_within_total(self) -> ProgressReported:
        if self.current is not None and self.total is not None and self.current > self.total:
            raise ValueError(f"progress current={self.current} is past total={self.total}")
        return self


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
    | ControlRequested
    | ControlObserved
    | TextDelta
    | ThoughtDelta
    | MessageCompleted
    | ToolCallStarted
    | ToolCallCompleted
    | NodeUpdated
    | ArtifactCreated
    | UsageReported
    | InputAppended
    | StatusReported
    | ProgressReported
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
    # the invocable the caller addressed, never the engine — an internal handoff (one
    # invocable delegating to another sub-agent inside its own run) does not change this;
    # "speaker" is defined at invocable granularity, not sub-agent granularity
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
    "Money",
    "ControlObserved",
    "ControlRequested",
    "ControlVerb",
    "Custom",
    "Event",
    "InputAppended",
    "KnownPayload",
    "MessageCompleted",
    "NodeUpdated",
    "ProgressReported",
    "RunCancelled",
    "RunCompleted",
    "RunContextSnapshot",
    "RunFailed",
    "RunInterrupted",
    "RunPaused",
    "RunResumed",
    "RunStarted",
    "SafePoint",
    "StatusReported",
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
