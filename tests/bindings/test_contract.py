"""The contract suite `docs/design/protocols/roadmap.md` names before an SPI v1: every line
proven either straight against `DeckGateway`/`Run` (fixture-free, see
`test_bindings_gateway.py` for the sibling style) or through `FixtureChannel`, the channel-shaped
plugin under `fixture_plugin/` built only on the public SPI.

Every wait is on a real event (`ScriptedModel.holding`, a spawned tail task, `RunSuspendedError`)
except one: the control gate's own documented batching window (`core/control.py`), which has no
event to wait on by design and is the same bounded wait `test_native_workflow.py`'s own pause
test needs.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fixture_plugin import FixtureChannel
from starlette.testclient import TestClient

from agentdeck import WorkflowCtx, workflow
from agentdeck.authoring import Agent
from agentdeck.bindings import (
    PROTOCOL_SPI_VERSION,
    BindingInfo,
    DeckGateway,
    GatewayError,
    GatewayFailureCode,
    HttpEndpoint,
)
from agentdeck.core.content import ImageBlock, TextBlock
from agentdeck.core.control import CONTROL_POLL_INTERVAL
from agentdeck.core.events import Event, UnknownEvent
from agentdeck.core.status import RunStatus
from agentdeck.deck import Deck
from agentdeck.errors import RunSuspendedError
from agentdeck.testing import ScriptedModel, patch_model

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_IMPORTLINTER = Path(__file__).parent / "fixture_plugin" / ".importlinter"
SECRET = "s3cr3t"


@pytest.fixture
def no_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


async def _survey(ctx: WorkflowCtx, topic: str) -> str:
    answer = await ctx.ask(f"pick a color for {topic}?", options=["red", "blue"])
    return f"{topic}:{answer}"


def _deck() -> Deck:
    return Deck(
        agents=[Agent(name="Greeter", instructions="Greet the user.")],
        workflows=[workflow(_survey, name="Survey")],
    )


def _channel(tmp_path: Path, *, target: str, name: str = "fixture", path: str = "/fixture") -> FixtureChannel:
    return FixtureChannel(secret=SECRET, map_path=tmp_path / "map.json", target=target, name=name, path=path)


# --- gateway/channel identity ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_receive_message_starts_a_run_reachable_through_ordinary_runs(no_project, tmp_path):
    """No `Runtime`, no store: `receive_message` reaches a Deck only through `gateway.start`,
    and the run it starts is an ordinary AgentDeck run, recoverable through `deck.runs` itself."""
    model = ScriptedModel(deltas=("hi",))
    deck = _deck()
    with patch_model(model):
        async with deck:
            channel = _channel(tmp_path, target="Greeter")
            channel.build(DeckGateway(deck))
            result = await channel.receive_message(
                secret=SECRET, conversation_id="c1", message_id="msg-1", content=TextBlock(text="hi")
            )
            recovered = await deck.runs.get(result["run_id"], namespace=result["namespace"])
            await next(iter(channel._tasks))

    assert recovered.id == result["run_id"]
    assert recovered.session_id == "fixture:c1"


@pytest.mark.asyncio
async def test_tail_posts_message_completed_to_the_outbox(no_project, tmp_path):
    model = ScriptedModel(deltas=("hi",))
    deck = _deck()
    with patch_model(model):
        async with deck:
            channel = _channel(tmp_path, target="Greeter")
            channel.build(DeckGateway(deck))
            await channel.receive_message(
                secret=SECRET, conversation_id="c1", message_id="msg-1", content=TextBlock(text="hi")
            )
            await next(iter(channel._tasks))

    assert channel.outbox == [{"conversation_id": "c1", "text": "hi"}]


@pytest.mark.asyncio
async def test_a_disconnected_reader_does_not_cancel_the_run(no_project, tmp_path):
    """Cancelling the tail (the reader) must never reach the run itself: it keeps executing and
    completes on its own, proven by fetching it fresh and awaiting it out."""
    model = ScriptedModel(deltas=("hi",))
    deck = _deck()
    with patch_model(model):
        async with deck:
            channel = _channel(tmp_path, target="Greeter")
            gateway = DeckGateway(deck)
            channel.build(gateway)
            result = await channel.receive_message(
                secret=SECRET, conversation_id="c1", message_id="msg-1", content=TextBlock(text="hi")
            )
            tail = next(iter(channel._tasks))
            tail.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await tail

            run = await gateway.get_run(result["run_id"], namespace=result["namespace"])
            turn = await run

    assert turn.output == "hi"


@pytest.mark.asyncio
async def test_receive_button_answers_and_retails_the_resumed_segment_without_polling(no_project, tmp_path):
    """`run.interrupted` renders as buttons; the button answers through `run.answer()` and
    re-tails from `last_seq + 1`  -  `Run.events(follow=True)` itself waits through the
    suspension, so nothing here polls for the resumed segment (ruling 29)."""
    deck = _deck()
    async with deck:
        channel = _channel(tmp_path, target="Survey")
        gateway = DeckGateway(deck)
        channel.build(gateway)
        result = await channel.receive_message(
            secret=SECRET, conversation_id="c1", message_id="msg-1", content=TextBlock(text="kites")
        )
        run = await gateway.get_run(result["run_id"], namespace=result["namespace"])

        with pytest.raises(RunSuspendedError) as excinfo:
            await run
        assert excinfo.value.status is RunStatus.WAITING_ANSWER
        await next(iter(channel._tasks))  # the tail's own segment ends at the interrupt boundary

        interrupt_post = {"conversation_id": "c1", "question": "pick a color for kites?", "buttons": ["red", "blue"]}
        assert channel.outbox == [interrupt_post]
        seq_at_interrupt = channel._map.get("msg-1")["last_seq"]
        assert seq_at_interrupt > 0

        await channel.receive_button(secret=SECRET, message_id="msg-1", value="red")
        await next(iter(channel._tasks))  # re-tail, no new tail spawned until this line
        turn = await run

        # The discriminating assertion: re-tailing from `last_seq + 1` (not from 0) means the
        # resumed segment never re-walks the first one, so the interrupt is posted exactly once
        # -  a channel that replayed from scratch would double-post it here.
        assert channel.outbox == [interrupt_post]
        assert channel._map.get("msg-1")["last_seq"] > seq_at_interrupt

    assert turn == "kites:red"


@pytest.mark.asyncio
async def test_cancel_pause_resume_map_to_the_same_run(no_project, tmp_path):
    hold = asyncio.Event()
    model = ScriptedModel(deltas=("one", "two"), hold=hold)
    deck = _deck()
    with patch_model(model):
        async with deck:
            channel = _channel(tmp_path, target="Greeter")
            gateway = DeckGateway(deck)
            channel.build(gateway)
            result = await channel.receive_message(
                secret=SECRET, conversation_id="c1", message_id="msg-1", content=TextBlock(text="hi")
            )
            await model.holding.wait()

            run = await gateway.get_run(result["run_id"], namespace=result["namespace"])
            await run.pause()
            # The gate reuses its last answer for one interval (core/control.py); the same
            # bounded wait `test_native_workflow.py`'s own pause test needs, not a poll loop.
            await asyncio.sleep(CONTROL_POLL_INTERVAL)
            hold.set()
            with pytest.raises(RunSuspendedError) as excinfo:
                await run
            assert excinfo.value.status is RunStatus.PAUSED

            await run.resume()
            turn = await run

            # `run.cancel()` on an already-terminal run is a no-op by contract (Run.cancel's own
            # docstring): proves the same handle still reaches the same run after it is done.
            await run.cancel()

    assert turn.output


@pytest.mark.asyncio
async def test_two_bindings_share_one_deck_and_stop_in_reverse(no_project, tmp_path):
    """Two bindings, one Exposure, one shared gateway: `channel_b` sees the run `channel_a`
    started without ever touching it itself, and shutdown runs in the reverse of start order."""
    deck = _deck()
    map_path = tmp_path / "map.json"
    channel_a = FixtureChannel(secret=SECRET, map_path=map_path, target="Greeter", name="a", path="/a")
    channel_b = FixtureChannel(secret=SECRET, map_path=map_path, target="Greeter", name="b", path="/b")

    order: list[str] = []
    orig_a, orig_b = channel_a.stop, channel_b.stop

    async def stop_a() -> None:
        order.append("a")
        await orig_a()

    async def stop_b() -> None:
        order.append("b")
        await orig_b()

    channel_a.stop, channel_b.stop = stop_a, stop_b
    exposure = deck.expose(channel_a, channel_b)

    model = ScriptedModel(deltas=("hi",))
    with patch_model(model), TestClient(exposure.asgi()) as client:
        response = client.post(
            "/a/message",
            json={
                "secret": SECRET,
                "conversation_id": "c1",
                "message_id": "msg-1",
                "content": {"type": "text", "text": "hi"},
            },
        )
        assert response.status_code == 200
        body = response.json()

        same_run = await channel_b._gateway.get_run(body["run_id"], namespace=body["namespace"])
        assert same_run.id == body["run_id"]

    assert order == ["b", "a"]


# --- properties every canonical event / content boundary must hold -----------------------------


@pytest.mark.asyncio
async def test_protocol_metadata_never_appears_in_event_payloads(no_project, tmp_path):
    """``session_id`` legitimately carries the channel prefix (ruling 8); the *payload* is what
    must stay clean of binding-owned identifiers the durable map already holds."""
    model = ScriptedModel(deltas=("hi",))
    deck = _deck()
    with patch_model(model):
        async with deck:
            channel = _channel(tmp_path, target="Greeter")
            gateway = DeckGateway(deck)
            channel.build(gateway)
            result = await channel.receive_message(
                secret=SECRET, conversation_id="conv-98765", message_id="msg-13579", content=TextBlock(text="hi")
            )
            await next(iter(channel._tasks))
            run = await gateway.get_run(result["run_id"], namespace=result["namespace"])
            events = [event async for event in run.events()]

    dumped_payloads = " ".join(event.payload.model_dump_json() for event in events)
    assert SECRET not in dumped_payloads
    assert "msg-13579" not in dumped_payloads


def test_an_unknown_event_kind_is_skipped_not_raised(tmp_path):
    channel = _channel(tmp_path, target="Greeter")
    event = Event.model_validate(
        {
            "kind": "future.thing",
            "seq": 0,
            "run_id": "r1",
            "session_id": None,
            "namespace": None,
            "origin": "Greeter",
            "ts": datetime.now(UTC).isoformat(),
            "payload": {"kind": "future.thing", "raw_payload": {"anything": True}},
        }
    )
    assert isinstance(event.payload, UnknownEvent)

    channel._project(event, "no-such-message")  # must not raise

    assert channel.outbox == []


@pytest.mark.asyncio
async def test_unsupported_content_is_rejected_with_invalid_input_naming_the_part(no_project, tmp_path):
    deck = _deck()
    async with deck:
        channel = _channel(tmp_path, target="Greeter")
        channel.build(DeckGateway(deck))
        image = ImageBlock(media_type="image/png", data_b64=base64.b64encode(b"x").decode())

        with pytest.raises(GatewayError) as excinfo:
            await channel.receive_message(secret=SECRET, conversation_id="c1", message_id="msg-1", content=image)

    assert excinfo.value.code is GatewayFailureCode.INVALID_INPUT
    assert "image" in excinfo.value.message
    assert channel._map.get("msg-1") is None  # nothing started


@pytest.mark.asyncio
async def test_receive_button_with_an_unknown_message_id_is_not_found(no_project, tmp_path):
    deck = _deck()
    async with deck:
        channel = _channel(tmp_path, target="Greeter")
        channel.build(DeckGateway(deck))

        with pytest.raises(GatewayError) as excinfo:
            await channel.receive_button(secret=SECRET, message_id="no-such-message", value="red")

    assert excinfo.value.code is GatewayFailureCode.NOT_FOUND
    assert "no-such-message" in excinfo.value.message


def test_fixture_imports_no_private_module():
    lint_imports = Path(sys.executable).with_name("lint-imports")
    result = subprocess.run(
        [str(lint_imports), "--config", str(FIXTURE_IMPORTLINTER)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# --- lifecycle ownership -------------------------------------------------------------------------


@dataclass
class _Boom:
    """A binding whose ``start()`` always fails, to drive the rollback the real fixture sits
    beside  -  the same toy shape ``test_bindings_exposure.py`` uses for the same purpose."""

    info: BindingInfo = field(
        default_factory=lambda: BindingInfo(
            name="boom", kind="protocol", transport="http", spi_version=PROTOCOL_SPI_VERSION, advertises=frozenset()
        )
    )

    def build(self, gateway: object) -> HttpEndpoint:
        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        return HttpEndpoint(path="/boom", app=app)

    async def start(self) -> None:
        raise RuntimeError("boom")

    async def stop(self) -> None:
        pass


@pytest.mark.asyncio
async def test_partial_startup_rolls_back_and_stops_the_fixture(no_project, tmp_path):
    deck = _deck()
    channel = _channel(tmp_path, target="Greeter")
    stopped: list[bool] = []
    orig_stop = channel.stop

    async def _stop() -> None:
        stopped.append(True)
        await orig_stop()

    channel.stop = _stop
    exposure = deck.expose(channel, _Boom())

    with pytest.raises(RuntimeError, match="boom"), TestClient(exposure.asgi()):
        pass

    assert stopped == [True]
    assert channel._tasks == set()
    assert not deck.is_open


@pytest.mark.asyncio
async def test_stop_cancels_every_in_flight_tail_task(no_project, tmp_path):
    hold = asyncio.Event()
    model = ScriptedModel(deltas=("hi", "there"), hold=hold)
    deck = _deck()
    with patch_model(model):
        async with deck:
            channel = _channel(tmp_path, target="Greeter")
            channel.build(DeckGateway(deck))
            await channel.receive_message(
                secret=SECRET, conversation_id="c1", message_id="msg-1", content=TextBlock(text="hi")
            )
            await model.holding.wait()

            assert len(channel._tasks) == 1
            await channel.stop()

    assert channel._tasks == set()


@pytest.mark.asyncio
async def test_the_webhook_acks_before_the_first_event_arrives(no_project, tmp_path):
    """The only place ACK-before-first-event is observable: a real POST through
    `exposure.asgi()`. The model is held after its first delta and never released, so a
    handler that waited for `message.completed` would hang this test forever instead of
    returning 200."""
    deck = _deck()
    channel = _channel(tmp_path, target="Greeter")
    exposure = deck.expose(channel)
    hold = asyncio.Event()
    model = ScriptedModel(deltas=("hi", "there"), hold=hold)
    with patch_model(model), TestClient(exposure.asgi()) as client:
        response = client.post(
            "/fixture/message",
            json={
                "secret": SECRET,
                "conversation_id": "c1",
                "message_id": "msg-1",
                "content": {"type": "text", "text": "hi"},
            },
        )
        assert response.status_code == 200

    assert channel.outbox == []


@pytest.mark.asyncio
async def test_the_webhook_maps_a_bad_secret_and_unsupported_content_to_http_errors(no_project, tmp_path):
    """The one gap the ACK test above leaves: `_http_message`'s own error branches, only
    observable through a real request  -  a direct `receive_message` call raises Python
    exceptions the handler never gets a chance to translate."""
    deck = _deck()
    channel = _channel(tmp_path, target="Greeter")
    exposure = deck.expose(channel)
    with TestClient(exposure.asgi()) as client:
        wrong_secret = client.post(
            "/fixture/message",
            json={
                "secret": "nope",
                "conversation_id": "c1",
                "message_id": "msg-1",
                "content": {"type": "text", "text": "hi"},
            },
        )
        assert wrong_secret.status_code == 401

        unsupported = client.post(
            "/fixture/message",
            json={
                "secret": SECRET,
                "conversation_id": "c1",
                "message_id": "msg-2",
                "content": {"type": "data", "data": {}},
            },
        )
        assert unsupported.status_code == 400
        assert unsupported.json()["error"] == "INVALID_INPUT"

    assert channel.outbox == []


@pytest.mark.asyncio
async def test_durable_map_survives_a_simulated_restart(no_project, tmp_path):
    """A second, independent `FixtureChannel` instance opened on the same path resolves and
    answers the run the first instance started and recorded  -  never sharing anything but the
    file (ruling 33)."""
    map_path = tmp_path / "map.json"
    deck = _deck()
    async with deck:
        gateway = DeckGateway(deck)
        channel_1 = FixtureChannel(secret=SECRET, map_path=map_path, target="Survey")
        channel_1.build(gateway)
        result = await channel_1.receive_message(
            secret=SECRET, conversation_id="c1", message_id="msg-1", content=TextBlock(text="kites")
        )
        run = await gateway.get_run(result["run_id"], namespace=result["namespace"])
        with pytest.raises(RunSuspendedError):
            await run
        await next(iter(channel_1._tasks))
        seq_at_interrupt = channel_1._map.get("msg-1")["last_seq"]

        # "restart": a brand new instance, no shared state but the file on disk.
        channel_2 = FixtureChannel(secret=SECRET, map_path=map_path, target="Survey")
        channel_2.build(gateway)
        await channel_2.receive_button(secret=SECRET, message_id="msg-1", value="red")
        await next(iter(channel_2._tasks))

        # `channel_2` starts with an empty outbox and never saw the interrupt itself: re-tailing
        # from `last_seq + 1` means it only walks the resumed segment, so it must stay empty  -
        # a restart that replayed from seq 0 would post the interrupt's buttons here instead.
        assert channel_2.outbox == []
        assert channel_1._map.get("msg-1")["last_seq"] > seq_at_interrupt

        entry = channel_1._map.get("msg-1")
        final = await gateway.get_run(entry["run_id"], namespace=entry["namespace"])
        turn = await final

    assert turn == "kites:red"
