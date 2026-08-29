"""A channel-shaped out-of-tree plugin (``docs/design/protocols/rulings.md`` 19, 33): built only
on ``agentdeck.bindings``, ``agentdeck.core.events``, ``agentdeck.core.content`` and
``agentdeck.errors`` (enforced by this package's own ``.importlinter``). Ack-then-continue, no
streaming advertised, a durable message-id to run map that survives a restart  -  the reference
shape ``tests/bindings/test_contract.py`` proves the SPI against.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from agentdeck.bindings import PROTOCOL_SPI_VERSION, BindingInfo, GatewayError, GatewayFailureCode, HttpEndpoint
from agentdeck.core.content import ContentBlock, TextBlock

if TYPE_CHECKING:
    from pathlib import Path

    from starlette.requests import Request

_CONTENT = TypeAdapter(ContentBlock)

_STATUS = {
    GatewayFailureCode.NOT_FOUND: 404,
    GatewayFailureCode.INVALID_INPUT: 400,
    GatewayFailureCode.CONFLICT: 409,
    GatewayFailureCode.BUSY: 409,
    GatewayFailureCode.UNSUPPORTED: 400,
    GatewayFailureCode.INTERNAL: 500,
}


def _error_response(error: PermissionError | GatewayError) -> JSONResponse:
    if isinstance(error, PermissionError):
        return JSONResponse({"error": "PERMISSION_DENIED", "message": str(error)}, status_code=401)
    return JSONResponse({"error": error.code.name, "message": error.message}, status_code=_STATUS[error.code])


class _DurableMap:
    """``message_id -> {namespace, run_id, session_id, conversation_id, last_seq}``, one JSON
    file. Every read loads the whole file and every write saves it back whole, so a second
    ``_DurableMap`` opened on the same path (a simulated restart) sees exactly what the first
    one wrote  -  no in-process cache to go stale.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        if not path.exists():
            path.write_text("{}")

    def _read(self) -> dict[str, Any]:
        return json.loads(self._path.read_text())

    def _write(self, data: dict[str, Any]) -> None:
        self._path.write_text(json.dumps(data))

    def put(
        self, message_id: str, *, namespace: str | None, run_id: str, session_id: str, conversation_id: str
    ) -> None:
        data = self._read()
        data[message_id] = {
            "namespace": namespace,
            "run_id": run_id,
            "session_id": session_id,
            "conversation_id": conversation_id,
            "last_seq": 0,
        }
        self._write(data)

    def get(self, message_id: str) -> dict[str, Any] | None:
        return self._read().get(message_id)

    def set_last_seq(self, message_id: str, seq: int) -> None:
        data = self._read()
        if message_id in data:
            data[message_id]["last_seq"] = seq
            self._write(data)


class FixtureChannel:
    """``kind="channel"``: a fake webhook in, ACK at once, an Exposure-owned tail, posts on
    ``message.completed``, buttons from ``run.interrupted``, a durable message-id to run map
    across a restart (``docs/design/protocols/rulings.md`` 31, 33). No streaming advertised.

    Constructed with everything it needs before a gateway exists, per :class:`Binding`'s real
    shape: :meth:`build` is where the gateway arrives, never the constructor  -  an out-of-tree
    author copies this file, so it has to be the canonical order.
    """

    def __init__(
        self, *, secret: str, map_path: Path, target: str, name: str = "fixture", path: str = "/fixture"
    ) -> None:
        self.info = BindingInfo(
            name=name,
            kind="channel",
            transport="fake",
            spi_version=PROTOCOL_SPI_VERSION,
            advertises=frozenset({"hitl"}),
            projects=frozenset({"run.interrupted", "message.completed"}),
        )
        self._secret = secret
        self._target = target
        self._path = path
        self._map = _DurableMap(map_path)
        self.outbox: list[dict[str, Any]] = []
        self._tasks: set[asyncio.Task[None]] = set()
        # Typed loosely on purpose: the fixture never imports ``agentdeck.deck`` (own
        # ``.importlinter`` contract), so ``DeckGateway``/``Run`` are reached only by
        # calling their public methods, never by naming their types.
        self._gateway: Any = None

    def build(self, gateway: Any) -> HttpEndpoint:
        """Pure: stores the gateway, wires two routes, opens nothing. Routes are bare
        (``/message``, ``/button``): the Exposure mounts this app at :attr:`_path` and
        Starlette's ``Mount`` already strips that prefix before it reaches here.
        """
        self._gateway = gateway
        app = Starlette(
            routes=[
                Route("/message", self._http_message, methods=["POST"]),
                Route("/button", self._http_button, methods=["POST"]),
            ]
        )
        return HttpEndpoint(path=self._path, app=app)

    async def start(self) -> None:
        """Nothing to prewarm: a tail task is spawned per inbound message, not once here."""

    async def stop(self) -> None:
        """Cancel and await every tail task this channel spawned, each in its own ``try`` so one
        task's exception never stops another's cancellation  -  the Exposure owns ``start``/
        ``stop``, never a task this binding did not hand it (ruling 32); this mirrors
        ``Exposure._lifecycle``'s own shutdown loop for the same reason. Re-raises the first
        non-``CancelledError`` any task raised, once every task has been cancelled and awaited,
        same as that shutdown loop.
        """
        tasks = list(self._tasks)
        first_error: BaseException | None = None
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except BaseException as error:
                first_error = first_error or error
        if first_error is not None:
            raise first_error

    async def receive_message(
        self, *, secret: str, conversation_id: str, message_id: str, content: Any
    ) -> dict[str, Any]:
        """The fake webhook: verify the secret, reject anything but text naming the part, start
        the run, record it in the durable map, spawn its tail, and return  -  never awaits the
        run itself, which is the whole ACK-then-continue point (ruling 33). Raises
        ``PermissionError`` for a bad secret, ``GatewayError`` for unsupported content or
        anything :meth:`DeckGateway.start` itself refuses.
        """
        if secret != self._secret:
            raise PermissionError("bad shared secret")
        if not isinstance(content, TextBlock):
            raise GatewayError(
                GatewayFailureCode.INVALID_INPUT, f"fixture channel supports text only, got {content.type!r}"
            )
        session_id = f"fixture:{conversation_id}"
        run = await self._gateway.start(self._target, content.text, session_id=session_id)
        self._map.put(
            message_id, namespace=run.namespace, run_id=run.id, session_id=session_id, conversation_id=conversation_id
        )
        self._spawn_tail(run, message_id, from_seq=0)
        return {"run_id": run.id, "namespace": run.namespace}

    async def receive_button(self, *, secret: str, message_id: str, value: Any) -> dict[str, Any]:
        """The later inbound button: resolve the run from the durable map, answer it, and
        re-tail from ``last_seq + 1``  -  no polling, ``Run.events(follow=True)`` waits through
        the suspension itself (ruling 29). Raises ``PermissionError`` for a bad secret,
        ``GatewayError(NOT_FOUND)`` for a ``message_id`` the durable map never heard of.
        """
        if secret != self._secret:
            raise PermissionError("bad shared secret")
        entry = self._map.get(message_id)
        if entry is None:
            raise GatewayError(GatewayFailureCode.NOT_FOUND, f"no run recorded for message {message_id!r}")
        run = await self._gateway.get_run(entry["run_id"], namespace=entry["namespace"])
        await run.answer(value)
        self._spawn_tail(run, message_id, from_seq=entry["last_seq"] + 1)
        return {"run_id": run.id}

    def _spawn_tail(self, run: Any, message_id: str, *, from_seq: int) -> None:
        task = asyncio.create_task(self._tail(run, message_id, from_seq=from_seq))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _tail(self, run: Any, message_id: str, *, from_seq: int) -> None:
        async for event in run.events(from_seq=from_seq, follow=True):
            self._map.set_last_seq(message_id, event.seq)
            self._project(event, message_id)

    def _project(self, event: Any, message_id: str) -> None:
        """What this channel does with one canonical event: post on ``message.completed``,
        render buttons on ``run.interrupted``, skip everything else  -  including a kind this
        version has never seen (:class:`~agentdeck.core.events.UnknownEvent`), which carries no
        ``kind`` this ``match`` recognises and so falls straight through.
        """
        payload = event.payload
        kind = getattr(payload, "kind", None)
        if kind not in ("message.completed", "run.interrupted"):
            return
        entry = self._map.get(message_id)
        conversation_id = entry["conversation_id"] if entry else None
        if kind == "message.completed":
            self.outbox.append({"conversation_id": conversation_id, "text": payload.text})
        else:
            options = payload.payload.get("options") or []
            self.outbox.append(
                {
                    "conversation_id": conversation_id,
                    "question": payload.payload.get("question"),
                    "buttons": list(options),
                }
            )

    async def _http_message(self, request: Request) -> JSONResponse:
        body = await request.json()
        try:
            content = _CONTENT.validate_python(body["content"])
            result = await self.receive_message(
                secret=body["secret"],
                conversation_id=body["conversation_id"],
                message_id=body["message_id"],
                content=content,
            )
        except (PermissionError, GatewayError) as error:
            return _error_response(error)
        return JSONResponse(result, status_code=200)

    async def _http_button(self, request: Request) -> JSONResponse:
        body = await request.json()
        try:
            result = await self.receive_button(
                secret=body["secret"], message_id=body["message_id"], value=body["value"]
            )
        except (PermissionError, GatewayError) as error:
            return _error_response(error)
        return JSONResponse(result, status_code=200)


__all__ = ["FixtureChannel"]
