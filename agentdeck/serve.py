"""Minimal HTTP surface: serve the ./.agentdeck project over FastAPI.

    agentdeck-serve                  # console script; HOST / PORT env override

Endpoints:
    GET  /health                     -> {"status": "ok", agents, workflows, skills}
                                        503 {"status": "starting"} before the lifespan runs
    POST /agents/{name}/chat         -> {"session_id", "message"} -> {"output"}
    POST /agents/{name}/chat?stream=true
                                      -> text/event-stream: "delta" events, then one
                                         "done" event carrying {"output", "usage"};
                                         an "error" event replaces "done" if the turn fails
    POST /workflows/{name}           -> JSON state in, final state out — or
                                        {"type": "interrupt", "payload", "thread_id"} when the
                                        run pauses on a human decision
    GET  /workflows/{name}/pending   -> [{"type": "interrupt", "payload", "thread_id"}, ...]
    POST /workflows/{name}/{thread_id}/resume
                                      -> {"value": ...} -> final state, or the next interrupt
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from agentdeck.agents.runners import StreamDone
from agentdeck.app import App
from agentdeck.errors import AgentdeckError, NotFoundError
from agentdeck.workflows.state import json_default

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI

logger = logging.getLogger(__name__)


def create_app() -> Any:
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import JSONResponse, StreamingResponse

    @asynccontextmanager
    async def lifespan(api: FastAPI) -> AsyncIterator[None]:
        # App.open() closes the Redis session client + MCP servers on shutdown
        # (including SIGTERM from `compose stop`), so no connection is leaked.
        async with App.open() as deck:
            api.state.deck = deck
            yield
            api.state.deck = None

    api = FastAPI(title="agentdeck", lifespan=lifespan)
    api.state.deck = None  # set by the lifespan; None means "not started yet"

    def deck() -> App:
        if api.state.deck is None:
            raise HTTPException(status_code=503, detail="agentdeck is not started")
        return api.state.deck

    # Starlette resolves a handler by walking the exception's MRO, so the
    # AgentdeckError entry below would already catch NotFoundError. It gets its
    # own entry because it is the one AgentdeckError caused by client input, and
    # so the only one whose message is safe to echo back.
    @api.exception_handler(NotFoundError)
    async def not_found(_request: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @api.exception_handler(AgentdeckError)
    async def internal_error(request: Request, exc: AgentdeckError) -> JSONResponse:
        # Every other AgentdeckError is a server-side fault, and its message can
        # carry secrets (skill stderr, config values) — log it, never ship it.
        logger.exception("%s serving %s", type(exc).__name__, request.url.path, exc_info=exc)
        return JSONResponse(status_code=500, content={"detail": "internal error"})

    @api.get("/health")
    async def health() -> Any:
        if api.state.deck is None:
            return JSONResponse({"status": "starting"}, status_code=503)
        return {"status": "ok", **api.state.deck.inventory}

    @api.post("/agents/{name}/chat")
    async def chat(name: str, body: dict[str, Any], stream: bool = False) -> Any:
        # Read the body up front: inside the generator a KeyError would surface as a
        # 200 that just stops, since the response headers are already on the wire.
        try:
            session_id, message = body["session_id"], body["message"]
        except KeyError as exc:
            raise HTTPException(status_code=422, detail=f"missing field: {exc.args[0]}") from exc
        app = deck()  # resolve before streaming so a pre-startup 503 keeps its status code
        if stream:

            async def events() -> AsyncIterator[str]:
                try:
                    async for chunk in app.chat_stream(name, session_id, message):
                        if isinstance(chunk, StreamDone):
                            # The SDK's own final_output — validated model for an output_type
                            # agent, last assistant message otherwise — not the re-joined
                            # deltas, which disagree for tool-using agents.
                            done = {"output": chunk.final_output, "usage": chunk.usage}
                            yield f"event: done\ndata: {json.dumps(done, default=json_default)}\n\n"
                        else:
                            yield f"data: {json.dumps({'delta': chunk})}\n\n"
                except Exception as exc:
                    # Mid-stream failures (max turns, guardrail trip, model error) can't change
                    # the status code any more; report them in-band without leaking internals.
                    yield f"event: error\ndata: {json.dumps({'error': type(exc).__name__})}\n\n"

            return StreamingResponse(
                events(),
                media_type="text/event-stream",
                # Proxies buffer streamed responses by default; nginx needs X-Accel-Buffering.
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        result = await app.chat(name, session_id, message)
        return {"output": result.final_output}

    @api.post("/workflows/{name}")
    async def run_workflow(name: str, state: dict[str, Any], thread_id: str | None = None) -> Any:
        out = await deck().run_workflow(name, state, thread_id=thread_id)
        return _jsonable(out)

    @api.get("/workflows/{name}/pending")
    async def pending_interrupts(name: str) -> Any:
        return _jsonable(await deck().pending_interrupts(name))

    @api.post("/workflows/{name}/{thread_id}/resume")
    async def resume_workflow(name: str, thread_id: str, body: dict[str, Any]) -> Any:
        if "value" not in body:
            raise HTTPException(status_code=422, detail="missing field: value")
        return _jsonable(await deck().resume_workflow(name, thread_id, body["value"]))

    return api


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=json_default))


def main() -> None:
    import uvicorn

    uvicorn.run(create_app(), host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "8000")))


if __name__ == "__main__":
    main()
