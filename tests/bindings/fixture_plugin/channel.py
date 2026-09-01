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

_CONTENT = TypeAdapter(ContentBlock)

_STATUS = {
    GatewayFailureCode.NOT_FOUND: 404,
    GatewayFailureCode.INVALID_INPUT: 400,
    GatewayFailureCode.CONFLICT: 409,
    GatewayFailureCode.BUSY: 409,
    GatewayFailureCode.UNSUPPORTED: 400,
    GatewayFailureCode.INTERNAL: 500,
}


async def _json(request: Request, *required: str) -> dict[str, Any]:
    """The body as a dict with every required key present, or ``INVALID_INPUT``: a plugin's HTTP
    edge is the one place a malformed request must not surface as a 500.
    """
    try:
        body = await request.json()
        missing = [key for key in required if key not in body]
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as error:
        raise GatewayError(GatewayFailureCode.INVALID_INPUT, f"malformed JSON body: {error}") from error
    if missing:
        raise GatewayError(GatewayFailureCode.INVALID_INPUT, f"body is missing {missing}")
    return body


def _content(raw: Any) -> ContentBlock:
    try:
        return _CONTENT.validate_python(raw)
    except ValidationError as error:
        raise GatewayError(GatewayFailureCode.INVALID_INPUT, f"unrecognized content: {error}") from error


def _error_response(error: PermissionError | GatewayError) -> JSONResponse:
    if isinstance(error, PermissionError):
        return JSONResponse({"error": "PERMISSION_DENIED", "message": str(error)}, status_code=401)
    return JSONResponse({"error": error.code.name, "message": error.message}, status_code=_STATUS[error.code])


class _DurableMap:
    """``message_id -> {namespace, run_id, session_id, conversation_id, last_seq}`` in one JSON
    file, read and written whole: a second instance on the same path (a restart) sees what the
    first wrote, with no in-process cache to go stale.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        if not path.exists():
            path.write_text("{}")

    def _read(self) -> dict[str, Any]:
        return json.loads(self._path.read_text())

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
            transport="fake",
            spi_version=PROTOCOL_SPI_VERSION,
            advertises=frozenset({"hitl"}),
        )
        self._secret = secret
        self._target = target
        self._path = path
        self._map = _DurableMap(map_path)
        self.outbox: list[dict[str, Any]] = []
        self._tasks: set[asyncio.Task[None]] = set()
        self._gateway: DeckGateway | None = None

    def build(self, gateway: DeckGateway) -> HttpEndpoint:
        """Store the gateway and wire the routes. Bare paths: the Exposure mounts this app at
        :attr:`_path` and the mount strips that prefix.
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
        """A tail task is spawned per inbound message, so there is nothing to prewarm."""

    async def stop(self) -> None:
        """Cancel and await every tail task, each in its own ``try``, then re-raise the first
        error any of them held. A task that already failed is still in the set: nothing discards
        it on completion, so its exception cannot go unretrieved.
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
        self._tasks.clear()
        if first_error is not None:
            raise first_error

    async def receive_message(
        self, *, secret: str, conversation_id: str, message_id: str, content: Any
    ) -> dict[str, Any]:
        """The webhook: start the run, record it, spawn its tail, return. Never awaits the run."""
        if secret != self._secret:
            raise PermissionError("bad shared secret")
        if not isinstance(content, TextBlock):
            raise GatewayError(
                GatewayFailureCode.INVALID_INPUT, f"fixture channel supports text only, got {content.type!r}"
            )
        session_id = f"fixture:{conversation_id}"
        run = await self._require_gateway().start(self._target, content.text, session_id=session_id)
        self._map.put(
            message_id, namespace=run.namespace, run_id=run.id, session_id=session_id, conversation_id=conversation_id
        )
        self._spawn_tail(run, message_id, from_seq=0)
        return {"run_id": run.id, "namespace": run.namespace}

    async def receive_button(self, *, secret: str, message_id: str, value: Any) -> dict[str, Any]:
        """Resolve the run from the durable map, answer it, re-tail from ``last_seq + 1``."""
        if secret != self._secret:
            raise PermissionError("bad shared secret")
        entry = self._map.get(message_id)
        if entry is None:
            raise GatewayError(GatewayFailureCode.NOT_FOUND, f"no run recorded for message {message_id!r}")
        run = await self._require_gateway().get_run(entry["run_id"], namespace=entry["namespace"])
        try:
            await run.answer(value)
        except ValueError as error:
            raise GatewayError(GatewayFailureCode.INVALID_INPUT, str(error), error) from error
        except RunStateError as error:
            raise GatewayError(GatewayFailureCode.CONFLICT, str(error), error) from error
        self._spawn_tail(run, message_id, from_seq=entry["last_seq"] + 1)
        return {"run_id": run.id}

    def _require_gateway(self) -> DeckGateway:
        if self._gateway is None:
            raise GatewayError(GatewayFailureCode.INTERNAL, "build() has not run: this binding has no gateway")
        return self._gateway

    def _spawn_tail(self, run: Any, message_id: str, *, from_seq: int) -> None:
        # No done callback: a task discarded on completion takes its exception with it, and
        # stop() is where this binding is contracted to surface it.
        self._tasks.add(asyncio.create_task(self._tail(run, message_id, from_seq=from_seq)))

    async def _tail(self, run: Any, message_id: str, *, from_seq: int) -> None:
        async for event in run.events(from_seq=from_seq, follow=True):
            self._map.set_last_seq(message_id, event.seq)
            self._project(event, message_id)

    def _project(self, event: Any, message_id: str) -> None:
        """Post on ``message.completed``, render buttons on ``run.interrupted``, skip the rest,
        an event kind this version has never seen included.
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
            self.outbox.append(
                {
                    "conversation_id": conversation_id,
                    "question": payload.payload.get("question"),
                    "buttons": list(payload.payload.get("options") or []),
                }
            )

    async def _http_message(self, request: Request) -> JSONResponse:
        try:
            body = await _json(request, "secret", "conversation_id", "message_id", "content")
            result = await self.receive_message(
                secret=body["secret"],
                conversation_id=body["conversation_id"],
                message_id=body["message_id"],
                content=_content(body["content"]),
            )
        except (PermissionError, GatewayError) as error:
            return _error_response(error)
        return JSONResponse(result, status_code=200)

    async def _http_button(self, request: Request) -> JSONResponse:
        try:
            body = await _json(request, "secret", "message_id", "value")
            result = await self.receive_button(
                secret=body["secret"], message_id=body["message_id"], value=body["value"]
            )
        except (PermissionError, GatewayError) as error:
            return _error_response(error)
        return JSONResponse(result, status_code=200)


__all__ = ["FixtureChannel"]
