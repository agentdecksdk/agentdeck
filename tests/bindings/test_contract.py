"""The SPI contract suite: `FixtureChannel`, an out-of-tree plugin built only on
`agentdeck.bindings`, driven against a real Deck.

Every wait is on a real event (`ScriptedModel.holding`, a spawned tail task, `RunSuspendedError`)
except one: the control gate's own documented batching window (`core/control.py`), which has no
event to wait on by design.
"""

from __future__ import annotations

import asyncio
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
    TextBlock,
)
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



@pytest.mark.asyncio
async def test_a_message_starts_an_ordinary_run_and_its_tail_posts_the_reply(no_project, tmp_path):
    """No `Runtime`, no store: the channel reaches a Deck only through `gateway.start`, the run
    it starts is recoverable through `deck.runs` itself, and the tail projects it."""
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
    re-tails from `last_seq + 1`. The first follow ends at the suspension and the re-tail blocks
    until the resumed segment writes, so nothing here polls (ruling 29)."""
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

        tails_before = set(channel._tasks)
        await channel.receive_button(secret=SECRET, message_id="msg-1", value="red")
        (retail,) = channel._tasks - tails_before  # the finished first tail stays in the set
        await retail
        turn = await run

        # The discriminating assertion: re-tailing from `last_seq + 1` (not from 0) means the
        # resumed segment never re-walks the first one, so the interrupt is posted exactly once
        # -  a channel that replayed from scratch would double-post it here.
        assert channel.outbox == [interrupt_post]
        assert channel._map.get("msg-1")["last_seq"] > seq_at_interrupt

    assert turn == "kites:red"


@pytest.mark.asyncio
async def test_two_bindings_share_one_deck_and_see_the_same_run(no_project, tmp_path):
    """Two bindings, one Exposure, one shared gateway: `channel_b` sees the run `channel_a`
    started without ever touching it itself."""
    deck = _deck()
    map_path = tmp_path / "map.json"
    channel_a = FixtureChannel(secret=SECRET, map_path=map_path, target="Greeter", name="a", path="/a")
    channel_b = FixtureChannel(secret=SECRET, map_path=map_path, target="Greeter", name="b", path="/b")
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
async def test_a_tail_that_failed_before_shutdown_still_raises_from_stop(no_project, tmp_path):
    """A task discarded on completion takes its exception with it: nothing to cancel at stop, no
    error to re-raise, and asyncio reports it as never retrieved instead."""
    model = ScriptedModel(deltas=("hi",))
    deck = _deck()
    with patch_model(model):
        async with deck:
            channel = _channel(tmp_path, target="Greeter")
            channel.build(DeckGateway(deck))

            async def exploding_tail(*args: object, **kwargs: object) -> None:
                raise RuntimeError("tail exploded")

            channel._tail = exploding_tail  # type: ignore[method-assign]
            await channel.receive_message(
                secret=SECRET, conversation_id="c1", message_id="msg-1", content=TextBlock(text="hi")
            )
            await asyncio.sleep(0)

            with pytest.raises(RuntimeError, match="tail exploded"):
                await channel.stop()


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
async def test_the_webhook_maps_every_rejection_to_an_http_code(no_project, tmp_path):
    """`_http_message`'s error branches, only observable through a real request: a direct
    `receive_message` call raises Python exceptions the handler never sees. Malformed input is
    the branch that matters, since the Native binding inherits this edge next."""
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
        assert "data" in unsupported.json()["message"]

        for body in ({"secret": SECRET}, {"secret": SECRET, "conversation_id": "c1", "message_id": "m", "content": 7}):
            malformed = client.post("/fixture/message", json=body)
            assert malformed.status_code == 400, body
            assert malformed.json()["error"] == "INVALID_INPUT"

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
