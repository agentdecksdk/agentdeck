"""The SPI contract suite: `FixtureChannel`, an out-of-tree plugin built only on the
public surface, driven against a real Deck.

Every wait is on a real event (`ScriptedModel.holding`, a spawned tail task, `RunSuspendedError`)
except one: the control gate's own documented batching window (`core/control.py`), which has no
event to wait on by design.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from fixture_plugin import FixtureChannel
from fixture_plugin.channel import _RequestError
from starlette.testclient import TestClient

from agentdeck import TextBlock, WorkflowCtx, workflow
from agentdeck.authoring import Agent
from agentdeck.bindings import DeckGateway
from agentdeck.core.events import Event, UnknownEvent
from agentdeck.core.status import RunStatus
from agentdeck.deck import Deck
from agentdeck.errors import RunSuspendedError
from agentdeck.testing import ScriptedModel, patch_model

if TYPE_CHECKING:
    from pathlib import Path

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
    """The channel reaches a Deck only through `gateway.start`, and what it starts is an
    ordinary run."""
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
    """The first follow ends at the suspension; the re-tail from `last_seq + 1` blocks until the
    resumed segment writes, so nothing polls (ruling 29)."""
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

        # Posted exactly once: a channel that re-tailed from 0 would double-post it here.
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

        with pytest.raises(_RequestError) as excinfo:
            await channel.receive_button(secret=SECRET, message_id="no-such-message", value="red")

    assert excinfo.value.status == 404
    assert "no-such-message" in excinfo.value.message


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
    """`_task_done` keeps the failure after discarding the task, so `stop()` can still raise it."""
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

        # Empty: `channel_2` walks only the resumed segment, never the first one.
        assert channel_2.outbox == []
        assert channel_1._map.get("msg-1")["last_seq"] > seq_at_interrupt

        entry = channel_1._map.get("msg-1")
        final = await gateway.get_run(entry["run_id"], namespace=entry["namespace"])
        turn = await final

    assert turn == "kites:red"
