"""The crude workflow surface: ``GET /pending`` + ``POST /resume``.

A second, additive FastAPI app — ``surfaces/serve/app.py`` (the chat SSE route) is not
touched by this module at all; a caller mounts both against the same ``Runtime``. Both
routes call only ``Runtime.pending``/``Runtime.resume``: this module never reads an
engine's execution state (checkpointer, SDK session) directly, which is what keeps that
state private to its engine. Same posture as ``app.py``: unnamespaced, crude.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from agentdeck.runtime.service import Runtime


class ResumeBody(BaseModel):
    """A missing field is a 422 from FastAPI, not a 500 mid-stream."""

    thread_id: str
    value: Any


def build_workflow_app(runtime: Runtime) -> FastAPI:
    """``GET /pending`` lists every waiting run; ``POST /resume`` answers one by
    ``thread_id``. A ``thread_id`` matching no pending run — unknown, or already resolved
    by a racing caller — is a no-op response, not a 404.
    """
    api = FastAPI()

    @api.get("/v2/pending")
    async def pending() -> list[dict[str, Any]]:
        listing = await runtime.pending()
        return [
            {
                "run_id": p.run_id,
                "session_id": p.session_id,
                "invocable": p.invocable,
                "thread_id": p.thread_id,
                "payload": p.payload,
            }
            for p in listing
        ]

    @api.post("/v2/resume")
    async def resume(body: ResumeBody) -> Any:
        match = next((p for p in await runtime.pending() if p.thread_id == body.thread_id), None)
        if match is None:
            return {"status": "no-op"}

        async def frames() -> AsyncIterator[str]:
            async for event in runtime.resume(
                match.invocable, body.thread_id, body.value, run_id=match.run_id, session_id=match.session_id
            ):
                yield f"data: {event.model_dump_json()}\n\n"

        return StreamingResponse(frames(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    return api


__all__ = ["build_workflow_app"]
