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
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from agentdeck.app import App
from agentdeck.workflows.state import json_default

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI


def create_app() -> Any:
    from fastapi import FastAPI

    inventory: dict[str, Any] = {}

    @asynccontextmanager
    async def lifespan(api: FastAPI) -> AsyncIterator[None]:
        # App.open() closes the Redis session client + MCP servers on shutdown
        # (including SIGTERM from `compose stop`), so no connection is leaked.
        async with App.open() as deck:
            api.state.deck = deck
            # open() already ran load(); re-running it here just to capture the
            # inventory for /health is idempotent (refresh=True) and cheap.
            inventory.update(deck.load())
            yield

    api = FastAPI(title="agentdeck", lifespan=lifespan)

    @api.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", **inventory}

    @api.post("/agents/{name}/chat")
    async def chat(name: str, body: dict[str, Any]) -> dict[str, Any]:
        result = await api.state.deck.chat(name, body["session_id"], body["message"])
        return {"output": result.final_output}

    @api.post("/workflows/{name}")
    async def run_workflow(name: str, state: dict[str, Any]) -> Any:
        out = await api.state.deck.run_workflow(name, state)
        return json.loads(json.dumps(out, default=json_default))

    return api


def main() -> None:
    import uvicorn

    uvicorn.run(create_app(), host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "8000")))


if __name__ == "__main__":
    main()
