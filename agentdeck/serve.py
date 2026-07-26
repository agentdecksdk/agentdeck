"""Minimal HTTP surface: serve the ./.agentdeck project over FastAPI.

    agentdeck-serve                  # console script; HOST / PORT env override

Endpoints:
    GET  /health                     -> {"status": "ok", agents, workflows, skills}
                                        503 {"status": "starting"} before the lifespan runs
    POST /agents/{name}/chat         -> {"session_id", "message"} -> {"output"}
    POST /workflows/{name}           -> JSON state in, final state out
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from agentdeck.app import App
from agentdeck.errors import AgentdeckError, NotFoundError
from agentdeck.workflows.state import json_default

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI

logger = logging.getLogger(__name__)


def create_app() -> Any:
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import JSONResponse

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
    async def chat(name: str, body: dict[str, Any]) -> dict[str, Any]:
        result = await deck().chat(name, body["session_id"], body["message"])
        return {"output": result.final_output}

    @api.post("/workflows/{name}")
    async def run_workflow(name: str, state: dict[str, Any]) -> Any:
        out = await deck().run_workflow(name, state)
        return json.loads(json.dumps(out, default=json_default))

    return api


def main() -> None:
    import uvicorn

    uvicorn.run(create_app(), host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "8000")))


if __name__ == "__main__":
    main()
