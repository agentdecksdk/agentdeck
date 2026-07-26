"""Minimal HTTP surface: serve the ./.agentdeck project over FastAPI.

    agentdeck-serve                  # console script; HOST / PORT env override

Endpoints:
    GET  /health                     -> {"status": "ok", agents, workflows, skills}
    POST /agents/{name}/chat         -> {"session_id", "message"} -> {"output"}
    POST /workflows/{name}           -> JSON state in, final state out
"""

from __future__ import annotations

import json
import os
from typing import Any

from agentdeck.app import App
from agentdeck.errors import AgentdeckError, NotFoundError
from agentdeck.workflows.state import json_default


def create_app() -> Any:
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse

    deck = App()
    inventory = deck.load()
    api = FastAPI(title="agentdeck")

    # Registered narrowest-first: FastAPI's exception_handlers dict resolves by
    # exact type, so NotFoundError needs its own entry even though it's also
    # an AgentdeckError.
    @api.exception_handler(NotFoundError)
    async def not_found(_request: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @api.exception_handler(AgentdeckError)
    async def agentdeck_error(_request: Request, exc: AgentdeckError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @api.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", **inventory}

    @api.post("/agents/{name}/chat")
    async def chat(name: str, body: dict[str, Any]) -> dict[str, Any]:
        result = await deck.chat(name, body["session_id"], body["message"])
        return {"output": result.final_output}

    @api.post("/workflows/{name}")
    async def run_workflow(name: str, state: dict[str, Any]) -> Any:
        out = await deck.run_workflow(name, state)
        return json.loads(json.dumps(out, default=json_default))

    return api


def main() -> None:
    import uvicorn

    uvicorn.run(create_app(), host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "8000")))


if __name__ == "__main__":
    main()
