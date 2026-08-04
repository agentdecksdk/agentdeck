"""Canonical event schema v1 — the one shape every engine, surface and store speaks.

Engines emit *payloads*; the Runtime stamps the *envelope*. That split is why an
engine cannot lie about ordering or tenancy.

**D8 — versioning.** A new ``kind``, or a new optional field on a payload, is a minor
change; renaming a field, removing one, or changing its meaning bumps ``Event.v``.
Consumers MUST ignore kinds they don't know, which :func:`parse_event` makes possible:
a v1 dashboard can render a stream from a v1.4 engine.

**D9 — the envelope is closed.** Eight fields. Per-run constants go into
``run.started`` (the join point), per-event data into its payload. An envelope addition
must demonstrate that routing, ordering or isolation is *impossible* without it.

**D10 — kinds are minted only here.** Engines translate into the kinds below or emit
``custom`` with a namespaced ``name``. Recurring use of one ``custom`` name is a signal
to promote it into core, never a precedent for minting kinds outside core.

**Ordering.** ``seq`` is per-run, contiguous from 0, assigned only by the Runtime, and
is the sole ordering authority — ``ts`` is informational. Contiguity makes loss
detectable (:func:`check_contiguous`). Persist-before-yield holds: an event a consumer
has seen is already in the store. A resumed run keeps its ``run_id`` and continues its
``seq``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Literal, get_args

from pydantic import AwareDatetime, Field, ValidationError, field_validator, model_validator

from agentdeck.core.content import CoreModel, Input

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

RESULT_PREVIEW_MAX = 4096

TERMINAL_KINDS = frozenset({"run.completed", "run.failed", "run.cancelled"})


class Usage(CoreModel):
    """Token and cost accounting. ``usd`` is None when no price is known for the model."""

    input_tokens: int
    output_tokens: int
    usd: float | None = None


class Budget(CoreModel):
    """The caps the run was admitted under; None means uncapped on that axis."""

    max_usd: float | None = None
    max_tokens: int | None = None


class RunContextSnapshot(CoreModel):
    """The parts of the run's context worth replaying from the event log alone."""

    principal: str
    trace_id: str
    budget: Budget | None = None
    triggered_by: str | None = None


class RunStarted(CoreModel):
    """The per-run join point: every per-run constant lives here, never on the envelope."""

    kind: Literal["run.started"] = "run.started"
    invocable: str
    kind_of_invocable: Literal["agent", "workflow", "skill"]
    parent_run_id: str | None = None
    input: Input
    context: RunContextSnapshot


class RunCompleted(CoreModel):
    """Terminal. ``usage`` here is the authoritative aggregate for the run."""

    kind: Literal["run.completed"] = "run.completed"
    output: Input
    usage: Usage


class RunFailed(CoreModel):
    """Terminal. ``error_code`` is a closed set so consumers can branch without parsing prose."""

    kind: Literal["run.failed"] = "run.failed"
    error_code: Literal["engine_error", "tool_error", "budget_exceeded", "deadline", "cancelled_hard"]
    message: str
    retryable: bool


class RunPaused(CoreModel):
    """Cooperative pause — not terminal, and not an interrupt awaiting an answer."""

    kind: Literal["run.paused"] = "run.paused"
    reason: str | None = None


class RunResumed(CoreModel):
    """The run continues after a pause or an interrupt, same ``run_id``, continuing ``seq``."""

    kind: Literal["run.resumed"] = "run.resumed"
    reason: str | None = None


class RunCancelled(CoreModel):
    """Terminal."""

    kind: Literal["run.cancelled"] = "run.cancelled"
    reason: str | None = None


class RunInterrupted(CoreModel):
    """Not terminal: the run keeps its ``run_id`` and continues its ``seq`` on resume."""

    kind: Literal["run.interrupted"] = "run.interrupted"
    interrupt_id: str
    reason: Literal["human", "pause", "approval"]
    payload: dict[str, Any]
    thread_id: str | None = None
    expected_resume: str | None = None


class TextDelta(CoreModel):
    """One streamed fragment of a message; the record is ``message.completed``."""

    kind: Literal["text.delta"] = "text.delta"
    message_id: str
    text: str


class ThoughtDelta(CoreModel):
    """Reasoning fragment — same framing as ``text.delta``, separate channel."""

    kind: Literal["thought.delta"] = "thought.delta"
    message_id: str
    text: str


class MessageCompleted(CoreModel):
    """The full final text — deltas are streaming UX, this is the record; one
    ``message_id`` never spans two origins."""

    kind: Literal["message.completed"] = "message.completed"
    message_id: str
    text: str


class ToolCallStarted(CoreModel):
    """Dispatch, paired with a ``tool.call.completed`` carrying the same ``call_id``."""

    kind: Literal["tool.call.started"] = "tool.call.started"
    call_id: str
    tool: str
    args: dict[str, Any]


class ToolCallCompleted(CoreModel):
    """Raw results never appear in events: a capped preview, a size, a hash, an artifact ref."""

    kind: Literal["tool.call.completed"] = "tool.call.completed"
    call_id: str
    tool: str
    result_preview: str
    result_size: int
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
    """``state_patch`` shallow-merges into the workflow state — top-level keys replace."""

    kind: Literal["node.updated"] = "node.updated"
    node: str
    state_patch: dict[str, Any]


class ArtifactCreated(CoreModel):
    """By reference only — never inline bytes."""

    kind: Literal["artifact.created"] = "artifact.created"
    artifact_id: str
    media_type: str
    uri: str
    size: int


class UsageReported(CoreModel):
    """Per-model-call and advisory; the terminal aggregate on ``run.completed`` wins."""

    kind: Literal["usage.reported"] = "usage.reported"
    model: str
    usage: Usage


class InputAppended(CoreModel):
    """Mid-turn steering. Schema now, no producer yet."""

    kind: Literal["input.appended"] = "input.appended"
    input: Input
    source: str


class Custom(CoreModel):
    """The engine escape hatch (D10) — ``name`` must be namespaced."""

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
    """Payload of a kind this version does not know. Consumers skip it; stores persist it."""

    kind: str
    raw_payload: dict[str, Any]


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

# get_args peels the Annotated, then the union — so a new payload class is a one-line
# change here and KNOWN_KINDS follows it.
KNOWN_KINDS: frozenset[str] = frozenset(p.model_fields["kind"].default for p in get_args(get_args(KnownPayload)[0]))


class Event(CoreModel):
    """The closed envelope (D9): eight fields. New needs go into a payload or into
    ``run.started``; a field here requires showing that routing, ordering or isolation
    is impossible without it. Closed is a rule about what we add, enforced by review —
    on the read side an unknown envelope field is still ignored, never fatal (D8)."""

    v: int = 1
    kind: str
    seq: int
    run_id: str
    session_id: str | None = None
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
    """The single parsing entry point: an unknown ``kind`` keeps a fully validated
    envelope and an :class:`UnknownEvent` payload. A malformed *known* payload still
    raises — forward compatibility is not permission to accept garbage."""
    try:
        return Event.model_validate(data)
    except ValidationError:
        kind, payload = data.get("kind"), data.get("payload")
        if not isinstance(kind, str) or kind in KNOWN_KINDS or not isinstance(payload, dict):
            raise
        return Event.model_validate({**data, "payload": {"kind": kind, "raw_payload": payload}})


def check_contiguous(events: Iterable[Event]) -> list[int]:
    """Missing ``seq`` numbers for one run — contiguity from 0; duplicates are out of scope."""
    seqs = {event.seq for event in events}
    if not seqs:
        return []
    return [n for n in range(max(seqs) + 1) if n not in seqs]


def check_terminal(events: Sequence[Event]) -> str | None:
    """``None`` if exactly one terminal event closes the run, else what is wrong."""
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
