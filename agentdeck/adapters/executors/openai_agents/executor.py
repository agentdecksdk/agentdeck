"""The openai-agents engine: ``Executor`` over ``agents.Runner``.

``spec.native`` is the pre-built ``agents.Agent`` (handoffs and tools included)  -  this
adapter only runs it and translates its stream, per ``core/ports/engine.py``. Execution
state (the SDK session) is engine-private (ADR-D5): the session, not the log, is what
feeds the model. The log passed in as ``history`` is read for exactly one purpose  -  the
turn-start reconciliation in ``reconcile.py``, which repairs a session left behind by a
crash between the log write and the session write.

Input is multimodal (``_to_sdk_input`` maps ``TextBlock``/``ImageBlock``/``AudioBlock``/
``DataBlock`` onto the SDK's own canonical parts); output is not. Nothing in this run loop
produces an image or audio block  -  ``_run_completed`` only ever builds
``TextBlock``/``DataBlock``  -  so an agent can *see* a photo or a voice note and never *return*
one. Audio is chat-completions only: at the pinned ``openai-agents==0.17.0``/``openai==2.32.0``,
the Responses API's content list has no audio member, so an ``AudioBlock`` under
``use_responses=True`` raises rather than reaching the endpoint and coming back as an opaque 400.

A ``DataBlock`` renders as its own ``input_text`` part (``reconcile.render_data_block``:
``json.dumps(block.data, ensure_ascii=False)``, nothing wrapped around it  -  see ``_part_of``).
Each block is already a separate entry in the SDK's content list, so the boundary between it and
a neighbouring ``TextBlock`` is the API's own, not a delimiter this adapter invents  -  there is no
paired open/close token embedded data could spoof to escape early, the way a hand-rolled
``<context>...</context>`` preamble can be broken by a value that contains ``</context>``.
``ResourceBlock`` still raises: a URI is a pointer, not content, and rendering just the pointer
would let a caller believe the model saw the bytes at that address when it never fetched them.

The renderer lives in ``reconcile.py``, not here: a ``DataBlock`` now produces the same
``{"type": "input_text", "text": ...}`` shape a real ``TextBlock`` does, so the SDK session
stores it indistinguishably from one  -  and ``reconcile``'s log-side transcript has to render it
identically, or a turn that carried a ``DataBlock`` would look like a permanent divergence on
every later turn. One function, called from both sides, is what keeps that from drifting.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, aclosing, asynccontextmanager, nullcontext
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, cast

from agents import Agent, Runner
from pydantic import BaseModel

from agentdeck.adapters.executors.openai_agents.reconcile import reconcile, render_data_block
from agentdeck.adapters.executors.openai_agents.runconfig import RunSettings, build_run_config
from agentdeck.adapters.executors.openai_agents.sessions import ExecutionStore
from agentdeck.adapters.executors.openai_agents.translate import translate
from agentdeck.core.content import AudioBlock, DataBlock, ImageBlock, ResourceBlock, TextBlock, coerce_input
from agentdeck.core.control import ControlSignalled
from agentdeck.core.events import RunCompleted, Usage, UsageReported
from agentdeck.core.ports import Executor
from agentdeck.errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Sequence

    from agents.items import TResponseInputItem
    from agents.memory.session import Session
    from agents.result import RunResultStreaming
    from agents.usage import Usage as SDKUsage

    from agentdeck.core.content import ContentBlock, Input
    from agentdeck.core.context import RunContext
    from agentdeck.core.events import Event, KnownPayload
    from agentdeck.core.invocable import InvocableSpec

SandboxScope = Callable[[Agent[Any]], AbstractAsyncContextManager[Any]]
"""How this engine opens whatever sandbox an agent needs: given the agent, a scope yielding
the SDK ``sandbox`` handle for its run (or ``None``).

Injected rather than built here because a sandbox is a capability, not an engine concern  -
it becomes a port of its own in the next slice. Unset means no agent in this project needs
one, which is every code-first caller until it says otherwise."""


@dataclass(slots=True)
class Launch:
    """One run's SDK handle, plus whether this engine reached its terminal payload.

    ``finished`` exists because nothing on the SDK result can answer that question at the
    moment it is asked: a run abandoned mid-stream and a run that ended normally both arrive
    at ``_launch``'s exit already cancelled, so ``is_complete`` is true either way, and
    ``final_output`` is only *usually* absent from the abandoned one (the SDK's run loop is
    detached, so it may well have finished while nobody was reading). The engine's own control
    flow is the authority, and this is how it says so.

    It is the *engine's* view, not the log's, and cannot be made the log's: it is set before the
    terminal payload is yielded, because the Runtime breaks on that payload and never returns
    here. So a store that rejects the terminal append leaves this ``True`` while the log ends in
    ``run.failed``  -  an observability span reporting success for a run the log calls failed. The
    log is the record; a reader reconciling the two believes the log.
    """

    result: RunResultStreaming
    finished: bool = False


class OpenAIAgentsExecutor(Executor):
    """Plays ``spec.native`` (an ``agents.Agent``) through ``Runner.run_streamed``.

    Everything a run is configured with arrives here already resolved  -  ``sessions`` is the
    conversation memory (Redis-backed or local), ``settings`` the endpoint and limits, and
    ``sandbox`` the scope an agent that needs one runs inside. All three default to the
    SDK's own behavior, so ``OpenAIAgentsExecutor()`` still runs an agent that configured
    itself.
    """

    name: ClassVar[str] = "openai-agents"
    suspendable: ClassVar[bool] = True

    def __init__(
        self,
        sessions: ExecutionStore | None = None,
        *,
        settings: RunSettings | None = None,
        sandbox: SandboxScope | None = None,
    ) -> None:
        self._sessions = sessions or ExecutionStore()
        self._settings = settings or RunSettings()
        self._sandbox = sandbox

    async def start(
        self,
        spec: InvocableSpec,
        input: Input,
        history: Sequence[Event],
        ctx: RunContext,
    ) -> AsyncGenerator[KnownPayload, None]:
        agent = _agent_of(spec)
        session = self._session(ctx)
        if session is not None:
            diverged = await reconcile(session, history)
            if diverged is not None:
                # Two stores disagreeing is worth a place in the record, not just a log line;
                # the run itself still has the session it needs and plays on.
                yield diverged
        message = _to_sdk_input(input, use_responses=self._settings.use_responses)
        async with self._launch(agent, message, ctx, session) as launch:
            result = launch.result
            tool_names: dict[str, str] = {}
            # The SDK's run loop is a detached task; an abandoned generator must cancel it
            # explicitly (mirrors agents/runners/headless.py's run_streamed, same reason).
            stream = cast("AsyncGenerator[Any, None]", result.stream_events())
            try:
                async with aclosing(stream) as events:
                    async for event in events:
                        payload = self._translate(event, tool_names, ctx.tool_failures)
                        if payload is not None:
                            yield payload
                        try:
                            await ctx.gate.checkpoint()
                        except ControlSignalled as signalled:
                            # A complete chunk was just yielded (or none was, at the very
                            # first safe point)  -  never a partial one  -  so this is the next
                            # safe point the contract promises, not "right now, mid-token".
                            # The SDK run is dropped either way: a paused turn has no
                            # checkpoint to sit in, so resuming replays it from the log.
                            result.cancel()
                            for payload in signalled.payloads:
                                yield payload
                            return
            except BaseException:
                result.cancel()
                raise
            result.cancel()
            terminal = self._terminal(result)
            # Set before the yields, not after them: the Runtime breaks on the terminal event,
            # so the line after this loop never runs.
            launch.finished = True
            for payload in terminal:
                yield payload

    def _session(self, ctx: RunContext) -> Session | None:
        """The execution state this run reads and writes  -  the adapter's own store by default."""
        return self._sessions.session_for(ctx)

    @asynccontextmanager
    async def _launch(
        self, agent: Agent[Any], message: str | list[TResponseInputItem], ctx: RunContext, session: Session | None
    ) -> AsyncIterator[Launch]:
        """Start the run and hold whatever scope it needs open until the stream is drained.

        Lifecycle rule: **code after the ``yield`` may never run.** A successful run ends
        with the Runtime breaking on the terminal event, which closes this generator  -  the
        ``yield`` raises ``GeneratorExit`` and the lines below it are skipped. Anything that
        must happen once per finished run therefore belongs in the ``GeneratorExit`` path,
        keyed on ``Launch.finished``, never only after the ``yield``.
        """
        scope = self._sandbox(agent) if self._sandbox is not None else nullcontext(None)
        async with scope as sandbox:
            yield Launch(
                Runner.run_streamed(
                    agent,
                    message,
                    # The run context travels as the SDK's own context object, which is the one thing
                    # the SDK hands a function tool: a tool declaring ``RunContextWrapper[RunContext]``
                    # reaches ``wrapper.context.reporter`` (and the gate) without importing a Runtime.
                    # Nothing in the SDK reads it  -  it is opaque to the run loop by design.
                    context=ctx,
                    session=session,
                    run_config=build_run_config(self._settings, sandbox=sandbox),
                    max_turns=self._settings.max_turns,
                )
            )

    def _translate(self, event: Any, tool_names: dict[str, str], tool_failures: dict[str, str]) -> KnownPayload | None:
        payload = translate(event, tool_names, tool_failures)
        return payload if payload is not None else _usage_reported(event)

    def _terminal(self, result: RunResultStreaming) -> Sequence[KnownPayload]:
        return (_run_completed(result),)

    async def resume(
        self,
        spec: InvocableSpec,
        thread_id: str,
        value: Any,
        ctx: RunContext,
    ) -> AsyncGenerator[KnownPayload, None]:
        # M0 scope is UC1's plain chat, which never suspends  -  there is no interrupted run
        # for this engine to continue. Raising (not a silent no-op) matches the Runtime's
        # own rule that this method is only ever called on a WAITING_ANSWER run.
        raise ConfigError(f"openai-agents engine (M0) has no interrupts to resume: {spec.name!r} never suspends")
        yield  # pragma: no cover  -  makes this an async generator; never reached


def _usage_reported(event: Any) -> KnownPayload | None:
    """One finished model call → one ``usage.reported``.

    The terminal event's ``usage`` is the SDK's cumulative total for the turn, so without
    this a consumer cannot tell one model call from four  -  which is exactly what v1's
    ``usage.requests`` counted.
    """
    if event.type != "raw_response_event" or getattr(event.data, "type", None) != "response.completed":
        return None
    response = event.data.response
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    return UsageReported(
        model=str(getattr(response, "model", "") or ""),
        usage=Usage(input_tokens=usage.input_tokens, output_tokens=usage.output_tokens),
    )


def _agent_of(spec: InvocableSpec) -> Agent[Any]:
    if not isinstance(spec.native, Agent):
        raise ConfigError(f"{spec.name!r} has no openai-agents Agent: expected native=Agent, got {type(spec.native)}")
    return spec.native


def _to_sdk_input(input: Input, *, use_responses: bool) -> str | list[TResponseInputItem]:
    """All-text input still returns the joined ``str`` it always has, byte for byte  -  every
    existing session item, reconcile transcript and stored event stays unchanged, and only a
    turn that actually carries media takes the branch below.

    That branch emits the SDK's own canonical (Responses) item shape  -  ``input_text`` /
    ``input_image`` / ``input_audio`` parts  -  rather than a converter agentdeck writes itself:
    ``agents.models.chatcmpl_converter.Converter`` already accepts these parts and maps them
    down to Chat-Completions parts, so one emitted shape works on both API paths.
    """
    texts = [block.text for block in input if isinstance(block, TextBlock)]
    if len(texts) == len(input):
        return "\n".join(texts)
    item = {"role": "user", "content": [_part_of(block, use_responses=use_responses) for block in input]}
    return cast("list[TResponseInputItem]", [item])


def _part_of(block: ContentBlock, *, use_responses: bool) -> dict[str, Any]:
    if isinstance(block, TextBlock):
        return {"type": "input_text", "text": block.text}
    if isinstance(block, ImageBlock):
        return {"type": "input_image", "image_url": f"data:{block.media_type};base64,{block.data_b64}"}
    if isinstance(block, AudioBlock):
        if use_responses:
            raise ConfigError(
                "openai-agents engine cannot send an 'audio' block over the Responses API: "
                "ResponseInputMessageContentListParam carries no audio member at this pin "
                "(openai-agents==0.17.0)  -  set use_responses=False (chat-completions) to send audio"
            )
        return {
            "type": "input_audio",
            "input_audio": {"data": block.data_b64, "format": _audio_format(block.media_type)},
        }
    if isinstance(block, DataBlock):
        return {"type": "input_text", "text": render_data_block(block)}
    if isinstance(block, ResourceBlock):
        raise ConfigError(
            "openai-agents engine cannot send a 'resource' block to the model: "
            f"{block.uri!r} is a pointer, not content  -  the engine never fetches it, so sending "
            "the URI alone risks the caller believing the model saw bytes it never received; "
            "read the resource and send its bytes as a text, image, audio, or data block instead"
        )
    raise ConfigError(
        f"openai-agents engine cannot send a {block.type!r} block to the model; "
        "it accepts text, image, audio (chat-completions only), and data input blocks"
    )


def _audio_format(media_type: str) -> str:
    """``audio/ogg; codecs=opus`` (a WhatsApp voice note's own media type) becomes ``ogg``: the
    subtype with parameters stripped, unvalidated against openai's own ``Literal["mp3", "wav"]``
     -  the chat-completions converter passes the string through unchanged, and a provider such
    as Gemini's OpenAI-compatible endpoint accepts ``ogg``."""
    return media_type.split(";", 1)[0].strip().rsplit("/", 1)[-1]


def _run_completed(result: RunResultStreaming) -> RunCompleted:
    output = result.final_output
    if isinstance(output, str):
        return RunCompleted(output=coerce_input(output), usage=_usage_of(result))
    return RunCompleted(output=[DataBlock(data=_structured(output))], usage=_usage_of(result))


def _structured(output: Any) -> Any:
    """An ``output_type`` agent's validated result as JSON data.

    It travels as a ``DataBlock``, which is why this no longer raises: refusing a non-``str``
    final output turned a documented feature into a failed run. The ceiling, and it applies to
    every branch below: a leaf JSON cannot carry becomes its ``str()``  -  a non-finite float
    included, since ``null`` would claim it was absent  -  rather than failing the run at its
    last event.
    """
    if isinstance(output, BaseModel):
        try:
            output = output.model_dump(mode="json")
        except ValueError:
            # PydanticSerializationError, which is a ValueError: one leaf pydantic cannot
            # render as JSON. The python dump keeps the rest and the net below takes that
            # leaf, so only its fidelity is lost  -  not the whole run's terminal event.
            output = output.model_dump()
    elif dataclasses.is_dataclass(output) and not isinstance(output, type):
        output = dataclasses.asdict(output)
    return json.loads(json.dumps(output, default=str), parse_constant=str)


def _usage_of(result: RunResultStreaming) -> Usage:
    usage: SDKUsage | None = getattr(result.context_wrapper, "usage", None)
    if usage is None:
        return Usage(input_tokens=0, output_tokens=0)
    return Usage(input_tokens=usage.input_tokens, output_tokens=usage.output_tokens)


__all__ = ["Launch", "OpenAIAgentsExecutor", "SandboxScope"]
