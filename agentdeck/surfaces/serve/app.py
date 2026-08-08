"""One crude SSE route for v2 runs — separate from v1's ``serve.py``, which stays
untouched. No auth, no discovery: the composition root hands in an already-wired
``Runtime``. Skeleton component: hardened or discarded at the M0 review, not polished now.
"""

from __future__ import annotations

import uuid
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from agentdeck.core.content import coerce_input
from agentdeck.core.context import RunContext
from agentdeck.errors import SessionBusyError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from agentdeck.core.events import Event
    from agentdeck.runtime.service import Runtime


class ChatBody(BaseModel):
    """Validated at the trust boundary instead of a bare ``body["session_id"]`` — a
    missing field is a 422 from FastAPI, not a 500 mid-stream."""

    # Non-empty because ``RunContext.log_key`` is ``session_id or run_id``: an empty one is
    # not an error anywhere downstream, it silently gives the turn a private log of its own,
    # so the caller's next message finds no history and nothing anywhere says why.
    session_id: str = Field(min_length=1)
    message: str


def build_app(runtime: Runtime) -> FastAPI:
    """The whole surface: one route, streaming ``Event.model_dump_json()`` lines.

    No ``_jsonable`` reshaping like v1's ``serve.py`` — the wire *is* the canonical event,
    which is the point of having one. A turn asked for on a session that already has one gets
    **409** with the holding run named, because that answer has to arrive before the stream does.
    """
    api = FastAPI()

    @api.post("/v2/invocables/{name}/chat")
    async def chat(name: str, body: ChatBody) -> Any:
        ctx = RunContext(
            run_id=str(uuid.uuid4()),
            trace_id=str(uuid.uuid4()),
            session_id=body.session_id,
        )

        stream = runtime.run(name, coerce_input(body.message), ctx)
        try:
            # The turn is claimed on the first event, so it has to be pulled here: once a
            # StreamingResponse has committed 200 and `text/event-stream`, a refusal can only
            # reach the client as a body that stops, which is indistinguishable from a run that
            # produced nothing — the one outcome raising instead of yielding exists to avoid.
            opening = await anext(stream)
        except SessionBusyError as busy:
            return JSONResponse(status_code=HTTPStatus.CONFLICT, content={"detail": str(busy)})

        async def frames() -> AsyncIterator[str]:
            yield _frame(opening)
            async for event in stream:
                yield _frame(event)

        return StreamingResponse(frames(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    return api


def _frame(event: Event) -> str:
    return f"data: {event.model_dump_json()}\n\n"


__all__ = ["build_app"]
