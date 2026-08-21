"""Event schema v1: one envelope, one payload per kind.

Engines produce payloads, the Runtime fills the envelope  -  so an engine cannot get
``seq`` or ``namespace`` wrong. ``seq`` is per-run and contiguous from 0, which makes it
both the ordering authority and a loss check; ``ts`` is informational.

Unknown kinds parse instead of raising  -  ``Event.model_validate`` lands them as
:class:`UnknownEvent`  -  and unknown fields inside a known payload are dropped, so an old reader
survives a newer writer. Adding a kind or an optional field is a ``minor`` bump, tolerated by
construction through :class:`UnknownEvent`/``UnknownBlock`` without either side consulting the
number; renaming, removing, or otherwise changing what a reader must already understand to parse
at all is a ``major`` bump, and :class:`Event` refuses any major but its own.

Run control is three phases, not one event: ``control.requested`` (the signal was written),
``control.observed`` (the run reached a safe point and acted), and the verb's own kind for the
effect. One pair of kinds carries every verb, so pause and steering need no vocabulary of their
own  -  and a caller can tell "the signal is recorded" from "the run actually stopped", which is
the whole difference between cooperative control and control.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, get_args

from pydantic import (
    AwareDatetime,
    ConfigDict,
    Field,
    NonNegativeInt,
    ValidationError,
    ValidatorFunctionWrapHandler,
    field_validator,
    model_validator,
)

from agentdeck.core.base import CoreModel, JsonData
from agentdeck.core.content import Input  # noqa: TC001  -  pydantic resolves field annotations at runtime

RESULT_PREVIEW_MAX = 4096

TERMINAL_KINDS = frozenset({"run.completed", "run.failed", "run.cancelled"})

KIND_PATTERN = r"^[a-z][a-z0-9]*(\.[a-z][a-z0-9]*)*$"
"""Dotted lowercase segments, digits allowed after the first letter so a namespace like ``a2a.*``
stays reachable (#129).

A shape, deliberately not a set: closing it to :data:`KNOWN_KINDS` would reject every kind a newer
writer invents, the one thing :class:`UnknownEvent` exists to prevent. This refuses only what no
writer should emit  -  ``""``, ``"Run Started"``, ``"run..started"``.
"""


class SchemaVersion(CoreModel):
    """The envelope's version, not a free-form semver: ``major`` is what a reader must already
    understand to parse the envelope at all, ``minor`` is an addition an old reader tolerates by
    construction  -  a kind it has never seen lands as ``UnknownEvent``, a block it has never seen
    lands as ``UnknownBlock``, neither consulting this number to do so. ``minor`` exists to record
    that provenance, not to gate parsing: :class:`Event` checks ``major`` and nothing else.

    A plain value type on purpose: it can represent a version this reader does not accept
    (``major=99``), because rejecting one is :class:`Event`'s decision, not this model's.
    """

    major: NonNegativeInt
    minor: NonNegativeInt


CURRENT_VERSION = SchemaVersion(major=4, minor=1)
"""What this tree writes onto every event.

``major=4`` (v5.0.0): the payload vocabulary moved, and v5.0.0 does not read a log v4 wrote. There
is no compatibility window and no store migration  -  replay a 4.x log into a new store, or read it
with the version that wrote it.

``minor=1``: :class:`AgentChanged` was added (#249), additive by construction  -  an older reader
degrades an event it carries to :class:`UnknownEvent` rather than failing.

A future additive change (a new kind, a new optional field) bumps ``minor`` here and nowhere else;
a breaking one bumps ``major``."""

Money = Annotated[float, Field(ge=0, allow_inf_nan=False)]
"""US dollars, constrained where the token counts already were. ``NaN``/``±Infinity`` serialize
as ``null``, so a consumer reads *no cost* where the producer wrote nonsense  -  and money is the
last field that should be exempt from the rule ``JsonData`` applies everywhere else."""


class Usage(CoreModel):
    """Token accounting. agentdeck does not price model calls  -  no provider returns dollars in a
    response, and a price depends on a contract, a tier and a date rather than on the call, so
    owning a table for every model on every provider would trade an empty field for a stale one.
    ``usd`` is reserved for a caller that supplies its own cost; agentdeck itself never sets it,
    and the field is slated for removal at the next major (#177)."""

    input_tokens: NonNegativeInt
    output_tokens: NonNegativeInt
    usd: Money | None = None


class RunStarted(CoreModel):
    """Opens a run: what was asked for, and what it was asked with.

    No context snapshot. Everything the old one carried  -  a trace id, a budget, who triggered
    it, a parent run  -  was recorded and read by nothing, so the log said a run was admitted
    under constraints that never existed. Where a run was played is on the envelope, where
    every event carries it.
    """

    kind: Literal["run.started"] = "run.started"
    invocable: str
    kind_of_invocable: Literal["agent", "workflow", "skill", "tool"]
    input: Input


class RunCompleted(CoreModel):
    """Terminal. ``usage`` is the authoritative total for the run.

    A structured result  -  a validated ``output_type``, a workflow's final state  -  is a
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
    """Cooperative pause  -  not terminal, and not waiting on an answer."""

    kind: Literal["run.paused"] = "run.paused"
    reason: str | None = None


class RunResumed(CoreModel):
    """The run continues: same ``run_id``, ``seq`` keeps counting.

    ``value`` is the answer, stored in full and riding on this event so that the write flipping
    ``WAITING_ANSWER`` to ``RUNNING`` is the same write that stores it  -  two writes leave a window
    where the log says a run was answered but no longer holds what the answer was. ``None`` is a
    resume answering nothing, which is what lifting a pause looks like.

    Not irreversible: the log is append-only and status is a fold over it, so a resume that cannot
    be carried through returns the run to ``WAITING_ANSWER`` by recording its interrupt again.
    """

    kind: Literal["run.resumed"] = "run.resumed"
    reason: str | None = None
    value: Input | None = None


class RunCancelled(CoreModel):
    """Terminal."""

    kind: Literal["run.cancelled"] = "run.cancelled"
    reason: str | None = None


InterruptReason = Literal["human", "pause", "approval"]
"""What a suspended run is waiting for. ``approval`` is the one with a shape: a yes or a no."""


class RunInterrupted(CoreModel):
    """Waiting on an answer. Not terminal  -  ``run_id`` and ``seq`` continue on resume."""

    kind: Literal["run.interrupted"] = "run.interrupted"
    interrupt_id: str
    reason: InterruptReason
    payload: dict[str, JsonData]
    thread_id: str | None = None
    expected_resume: str | None = None


ControlVerb = Literal["cancel", "pause", "resume", "steer"]
"""Every verb run control has, including the ones whose behavior isn't built yet.

Complete at birth deliberately: a member added later is not additive for a *reader*, which
rejects the whole event rather than skipping it the way it can an unknown kind.
"""

SafePoint = Literal["stream_item", "tool_dispatch", "node_boundary"]
"""Where a run can notice a signal: between two streamed items, before a tool is dispatched, or
at a node boundary. Closed for the same reason ``ControlVerb`` is, which is also why
``node_boundary`` stays after the last executor emitting it left: dropping a member rejects
every logged event that carries it."""


class ControlRequested(CoreModel):
    """A control signal was recorded for this run  -  not that the run has acted on it.

    Only this much is knowable when a caller asks: the run may be inside a tool call that has to
    return first. So a request is never a status transition  -  a run stays ``RUNNING`` until
    ``run.paused`` or ``run.cancelled`` says otherwise. A signal that lost the race with a
    terminal event records nothing: a terminal event is a run's last by invariant.
    """

    kind: Literal["control.requested"] = "control.requested"
    verb: ControlVerb
    reason: str | None = None


class ControlObserved(CoreModel):
    """The run reached a safe point, found the signal, and is acting on it  -  noticed, not stopped.
    The verb's own kind records the effect.

    ``safe_point`` names where, because "cancel took eight seconds" and "cancel took eight seconds
    because a tool call did" are different answers to the same complaint.
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
    """Reasoning fragment  -  same framing as ``text.delta``, separate channel."""

    kind: Literal["thought.delta"] = "thought.delta"
    message_id: str
    text: str


class MessageCompleted(CoreModel):
    """The full final text  -  deltas are streaming UX. One ``message_id`` never spans two origins."""

    kind: Literal["message.completed"] = "message.completed"
    message_id: str
    text: str


class AgentChanged(CoreModel):
    """Within this run and this conversation, the active agent changed: a handoff completed,
    never one merely requested. A handoff that failed or was refused leaves no event behind.

    ``previous_agent``/``next_agent`` rather than ``from_agent``/``to_agent``: ``from`` is
    reserved, and the pair reads better matched than split across a keyword workaround and an
    ordinary word. ``run_id`` and ``session_id`` on the envelope do not change; only this.
    """

    kind: Literal["agent.changed"] = "agent.changed"
    previous_agent: str
    next_agent: str


class ToolCallStarted(CoreModel):
    """Dispatch; paired with ``tool.call.completed`` by ``call_id``."""

    kind: Literal["tool.call.started"] = "tool.call.started"
    call_id: str
    tool: str
    args: dict[str, JsonData]


class ToolCallCompleted(CoreModel):
    """A capped preview plus size and hash, never the result itself."""

    kind: Literal["tool.call.completed"] = "tool.call.completed"
    call_id: str
    tool: str
    result_preview: str = Field(max_length=RESULT_PREVIEW_MAX)
    result_size: NonNegativeInt
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_id: str | None = None
    error: str | None = None


class ArtifactCreated(CoreModel):
    """A reference to bytes stored elsewhere."""

    kind: Literal["artifact.created"] = "artifact.created"
    artifact_id: str
    media_type: str
    uri: str
    size: NonNegativeInt


class UsageReported(CoreModel):
    """One model call, advisory  -  the total on ``run.completed`` wins."""

    kind: Literal["usage.reported"] = "usage.reported"
    model: str
    usage: Usage


class InputAppended(CoreModel):
    """Mid-turn steering. No producer yet."""

    kind: Literal["input.appended"] = "input.appended"
    input: Input
    source: str


class AnswerRefused(CoreModel):
    """An answer that never landed: it was not one of the options the run asked for.

    Not a lifecycle kind. The run is still ``WAITING_ANSWER`` after this, and the next answer can
    still land  -  which is the whole point of refusing before the claim rather than letting a
    mistyped reply resume the run and fail it.

    ``reason`` names the type that arrived and what was expected, never the value itself: the
    options are already on this run's ``run.interrupted``, and a refused answer can carry whatever
    the answerer typed  -  which must not reach a sink for the sake of an audit line.
    """

    kind: Literal["answer.refused"] = "answer.refused"
    reason: str


class Reported(CoreModel):
    """Advisory: what the running code chose to say about itself.

    Not a transition  -  status folds from the lifecycle kinds (``core/status.py``), so a run
    reporting ``"Searching GitHub"`` is still ``RUNNING``. And not an event about the runtime:
    every other kind here says what AgentDeck did, this one says what the application said.

    Four things a report can be, in one field rather than one kind each: three severities a
    person reads, and a structured record a pipeline filters. A record's ``message`` is its own
    name (``"candidate_found"``), so a reader that knows no schema still has something to show
    and a consumer that does filters on the same string.
    """

    kind: Literal["report"] = "report"
    level: Literal["info", "warning", "error", "record"] = "info"
    message: str = Field(min_length=1)
    fields: dict[str, JsonData] = Field(default_factory=dict)


class Custom(CoreModel):
    """Engine-specific event; ``name`` must be namespaced."""

    kind: Literal["custom"] = "custom"
    # ``<namespace>.<event>``, both non-empty; a further-dotted event name is the namespace's
    # own business. Pattern rather than a validator  -  pydantic already says it better than the
    # message a hand-written one would raise.
    name: str = Field(pattern=r"^[^.]+\..+$")
    data: dict[str, JsonData]


class UnknownEvent(CoreModel):
    """A kind this version doesn't know: consumers skip it, stores keep it.

    Strict on purpose  -  it sits in a union with the known payloads, so anything laxer
    would let a malformed known payload validate here instead of raising.
    """

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(pattern=KIND_PATTERN)
    raw_payload: dict[str, JsonData]

    @field_validator("kind")
    @classmethod
    def _is_not_a_known_kind(cls, value: str) -> str:
        if value in KNOWN_KINDS:
            raise ValueError(f"{value!r} is a known kind  -  use its payload class")
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
    | AgentChanged
    | ToolCallStarted
    | ToolCallCompleted
    | ArtifactCreated
    | UsageReported
    | InputAppended
    | Reported
    | AnswerRefused
    | Custom,
    Field(discriminator="kind"),
]

# derived by peeling the Annotated, then the union: a payload class added above follows here
KNOWN_KINDS: frozenset[str] = frozenset(p.model_fields["kind"].default for p in get_args(get_args(KnownPayload)[0]))


class Event(CoreModel):
    """The eight fields every event carries, whatever its kind. Per-run constants belong in the
    ``run.started`` payload, not here.

    ``model_validate`` is the only entry point a reader needs: an unfamiliar ``kind`` lands as
    :class:`UnknownEvent` rather than raising. A malformed *known* payload still raises.

    ``kind`` is written twice  -  here and inside the payload  -  because each copy answers a
    different question: this one is what a store indexes on without parsing (``json_extract(data,
    '$.kind')``), the payload's is the union's discriminator. Dropping either is a wire change: a
    released reader dispatches on the payload copy and cannot read a row without it.
    """

    v: SchemaVersion = CURRENT_VERSION
    kind: str = Field(pattern=KIND_PATTERN)
    seq: NonNegativeInt
    run_id: str
    session_id: str | None
    # the opaque isolation boundary the run was played in, or None when nothing is kept
    # apart; AgentDeck never reads its parts, only which events share it
    namespace: str | None
    # the invocable the caller addressed, never the engine: an internal handoff to a sub-agent
    # does not change it, because "speaker" is defined at invocable granularity
    origin: str
    ts: AwareDatetime
    payload: KnownPayload | UnknownEvent

    @field_validator("v", mode="before")
    @classmethod
    def _a_pre_v3_scalar_version_says_so(cls, value: object) -> object:
        """``v`` was a plain integer up to and including v3.0.0b1, so an event out of a store
        written then fails here rather than at :class:`SchemaVersion`'s own field parsing  -  where
        it reads as two unrelated missing-field errors and tells the operator nothing about why
        their log stopped loading."""
        if isinstance(value, int) and not isinstance(value, bool):
            raise ValueError(
                f"this event was written by schema v{value}, which stored `v` as a plain integer; "
                f"major {CURRENT_VERSION.major} stores it as {{major, minor}} and cannot read it. "
                "An event log written before v3.0.0 has to be replayed into a new store, or read "
                "with the version of agentdeck that wrote it."
            )
        return value

    @field_validator("v")
    @classmethod
    def _major_version_must_be_this_readers_own(cls, value: SchemaVersion) -> SchemaVersion:
        """Another major is refused outright rather than offered the unknown-kind path: that path
        only knows how to skip a kind it has never seen, not a wire shape it was never taught."""
        if value.major != CURRENT_VERSION.major:
            raise ValueError(
                f"event major version {value.major} unsupported, this reader writes and reads "
                f"major {CURRENT_VERSION.major}. An event log written by another major has to be "
                "replayed into a new store, or read with the version of agentdeck that wrote it."
            )
        return value

    @model_validator(mode="wrap")
    @classmethod
    def _an_unknown_kind_degrades(cls, data: Any, handler: ValidatorFunctionWrapHandler) -> Any:
        """An unfamiliar ``kind`` lands as :class:`UnknownEvent` instead of failing the whole read
         -  stores parse every row, so one unrecognised event would take a session's log with it.

        The payload's own claim is compared before wrapping, never overwritten: wrapping with the
        envelope's ``kind`` would *relabel* a row whose two copies disagree, and that disagreement
        is exactly what says the row is not what it claims to be.

        A stored ``UnknownEvent`` (``{kind, raw_payload}``) validates against the union member
        directly, so ``handler`` succeeds and it is never re-wrapped  -  which lets it round-trip.
        """
        try:
            return handler(data)
        except ValidationError:
            if not isinstance(data, dict):
                raise
            kind, payload = data.get("kind"), data.get("payload")
            if not isinstance(kind, str) or kind in KNOWN_KINDS or not isinstance(payload, dict):
                raise
            stated = payload.get("kind")
            if isinstance(stated, str) and stated != kind:
                raise ValueError(f"envelope kind {kind!r} does not match payload kind {stated!r}") from None
            return handler({**data, "payload": {"kind": kind, "raw_payload": payload}})

    @model_validator(mode="after")
    def _kind_mirrors_payload(self) -> Event:
        if self.kind != self.payload.kind:
            raise ValueError(f"envelope kind {self.kind!r} does not match payload kind {self.payload.kind!r}")
        return self
