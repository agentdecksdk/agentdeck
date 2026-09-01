"""A channel-shaped out-of-tree plugin (``docs/design/protocols/rulings.md`` 19, 33), built on
``agentdeck.bindings`` and ``agentdeck.errors`` alone, enforced by this package's own
``.importlinter``. Ack-then-continue, no streaming, a durable message-id to run map.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter, ValidationError
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from agentdeck.bindings import (
    PROTOCOL_SPI_VERSION,
    BindingInfo,
    ContentBlock,
    DeckGateway,
    GatewayError,
    GatewayFailureCode,
    HttpEndpoint,
    TextBlock,
)
from agentdeck.errors import RunStateError

if TYPE_CHECKING:
    from pathlib import Path

    from starlette.requests import Request

    from agentdeck import Event, Run

_CONTENT = TypeAdapter(ContentBlock)

_GATEWAY_STATUS = {
    GatewayFailureCode.NOT_FOUND: 404,
    GatewayFailureCode.INVALID_INPUT: 400,
    GatewayFailureCode.CONFLICT: 409,
    GatewayFailureCode.BUSY: 409,
    GatewayFailureCode.UNSUPPORTED: 400,
    GatewayFailureCode.INTERNAL: 500,
}


class _RequestError(Exception):
    """A request this channel refuses, as opposed to a :class:`GatewayError` from the Deck."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class _DurableMap:
    """File-backed ``message_id`` to run mapping, so a restart can reconstruct the Run."""

    def __init__(self, path: Path) -> None:
        self._path = path
        if not path.exists():
            path.write_text("{}")

    def _read(self) -> dict[str, Any]:
        return json.loads(self._path.read_text())

    def put(self, message_id: str, *, namespace: str | None, run_id: str, conversation_id: str) -> None:
        data = self._read()
        data[message_id] = {
            "namespace": namespace,
            "run_id": run_id,
            "conversation_id": conversation_id,
            "last_seq": 0,
        }
        self._path.write_text(json.dumps(data))

    def get(self, message_id: str) -> dict[str, Any] | None:
        return self._read().get(message_id)

    def set_last_seq(self, message_id: str, seq: int) -> None:
        data = self._read()
        if message_id in data:
            data[message_id]["last_seq"] = seq
            self._path.write_text(json.dumps(data))


class FixtureChannel:
    """``kind="channel"``: webhook in, ACK at once, tail the run, post on ``message.completed``,
    buttons from ``run.interrupted``, durable across a restart.
    """

    def __init__(
        self, *, secret: str, map_path: Path, target: str, name: str = "fixture", path: str = "/fixture"
    ) -> None:
        self.info = BindingInfo(
            name=name,
            kind="channel",
            transport="http",
            spi_version=PROTOCOL_SPI_VERSION,
            advertises=frozenset({"hitl"}),
        )
        self._secret = secret
        self._target = target
        self._path = path
        self._map = _DurableMap(map_path)
        self.outbox: list[dict[str, Any]] = []
        self._gateway: DeckGateway | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._task_error: BaseException | None = None

    def build(self, gateway: DeckGateway) -> HttpEndpoint:
        """Store the gateway and wire the routes. The mount strips :attr:`_path` before they run."""
        self._gateway = gateway
        routes = [Route("/message", self._http_message, methods=["POST"])]
        return HttpEndpoint(path=self._path, app=Starlette(routes=routes))

    async def start(self) -> None:
        """A tail is spawned per inbound message, so there is nothing to prewarm."""

    async def stop(self) -> None:
        """Drain the tails this channel owns and raise the first error any of them held."""
        first_error = self._task_error
        for task in list(self._tasks):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except BaseException as error:
                first_error = first_error or error
        self._tasks.clear()
        self._task_error = None
        if first_error is not None:
            raise first_error

    async def receive_message(
        self, *, secret: str, conversation_id: str, message_id: str, content: ContentBlock
    ) -> dict[str, Any]:
        """Start the run, record it, spawn its tail, return. Never awaits the run."""
        self._check(secret)
        if not isinstance(content, TextBlock):
            raise _RequestError(400, "INVALID_INPUT", f"this channel supports text only, got {content.type!r}")
        run = await self._require_gateway().start(
            self._target, content.text, session_id=f"fixture:{conversation_id}"
        )
        self._map.put(message_id, namespace=run.namespace, run_id=run.id, conversation_id=conversation_id)
        self._spawn_tail(run, message_id, from_seq=0)
        return {"run_id": run.id, "namespace": run.namespace}

    async def receive_button(self, *, secret: str, message_id: str, value: Any) -> dict[str, Any]:
        """Resolve the run from the durable map, answer it, re-tail from ``last_seq + 1``."""
        self._check(secret)
        entry = self._map.get(message_id)
        if entry is None:
            raise _RequestError(404, "NOT_FOUND", f"no run recorded for message {message_id!r}")
        run = await self._require_gateway().get_run(entry["run_id"], namespace=entry["namespace"])
        try:
            await run.answer(value)
        except ValueError as error:
            raise _RequestError(400, "INVALID_INPUT", str(error)) from error
        except RunStateError as error:
            raise _RequestError(409, "CONFLICT", str(error)) from error
        self._spawn_tail(run, message_id, from_seq=entry["last_seq"] + 1)
        return {"run_id": run.id}

    def _check(self, secret: str) -> None:
        if secret != self._secret:
            raise _RequestError(401, "PERMISSION_DENIED", "bad shared secret")

    def _require_gateway(self) -> DeckGateway:
        if self._gateway is None:
            raise _RequestError(500, "INTERNAL", "build() has not run: this binding has no gateway")
        return self._gateway

    def _spawn_tail(self, run: Run, message_id: str, *, from_seq: int) -> None:
        task = asyncio.create_task(self._tail(run, message_id, from_seq=from_seq))
        self._tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task[None]) -> None:
        """Keep the failure, drop the task: a channel may run millions of these."""
        self._tasks.discard(task)
        if task.cancelled():
            return
        if error := task.exception():
            self._task_error = self._task_error or error

    async def _tail(self, run: Run, message_id: str, *, from_seq: int) -> None:
        async for event in run.events(from_seq=from_seq, follow=True):
            self._map.set_last_seq(message_id, event.seq)
            self._project(event, message_id)

    def _project(self, event: Event, message_id: str) -> None:
        """Post on ``message.completed``, buttons on ``run.interrupted``, skip the rest, an event
        kind this version has never seen included.
        """
        payload = event.payload
        kind = getattr(payload, "kind", None)
        if kind not in ("message.completed", "run.interrupted"):
            return
        entry = self._map.get(message_id)
        conversation_id = entry["conversation_id"] if entry else None
        if kind == "message.completed":
            self.outbox.append({"conversation_id": conversation_id, "text": payload.text})
            return
        self.outbox.append(
            {
                "conversation_id": conversation_id,
                "question": payload.payload.get("question"),
                "buttons": list(payload.payload.get("options") or []),
            }
        )

    async def _http_message(self, request: Request) -> JSONResponse:
        try:
            body = await _json_object(request, "secret", "conversation_id", "message_id", "content")
            result = await self.receive_message(
                secret=body["secret"],
                conversation_id=body["conversation_id"],
                message_id=body["message_id"],
                content=_content(body["content"]),
            )
        except (_RequestError, GatewayError) as error:
            return _http_error(error)
        return JSONResponse(result, status_code=200)


async def _json_object(request: Request, *required: str) -> dict[str, Any]:
    """Parse an object body carrying every required field."""
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise _RequestError(400, "INVALID_INPUT", f"malformed JSON body: {error}") from error
    if not isinstance(body, dict):
        raise _RequestError(400, "INVALID_INPUT", "JSON body must be an object")
    if missing := [key for key in required if key not in body]:
        raise _RequestError(400, "INVALID_INPUT", f"body is missing {missing}")
    return body


def _content(raw: Any) -> ContentBlock:
    try:
        return _CONTENT.validate_python(raw)
    except ValidationError as error:
        raise _RequestError(400, "INVALID_INPUT", f"unrecognized content: {error}") from error


def _http_error(error: _RequestError | GatewayError) -> JSONResponse:
    """One wire shape for both sides: what this channel refused, and what the Deck refused."""
    if isinstance(error, _RequestError):
        return JSONResponse({"error": error.code, "message": error.message}, status_code=error.status)
    return JSONResponse(
        {"error": error.code.name, "message": error.message}, status_code=_GATEWAY_STATUS[error.code]
    )


__all__ = ["FixtureChannel"]
