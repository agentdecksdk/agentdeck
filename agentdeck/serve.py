"""Minimal HTTP surface: serve the ./.agentdeck project over FastAPI.

    agentdeck-serve                  # console script; HOST / PORT env override

Endpoints:
    GET  /health                     -> {"status": "ok", agents, workflows, skills}
    POST /agents/{name}/chat         -> {"session_id", "message"} -> {"output"}
    POST /agents/{name}/chat?stream=true
                                      -> text/event-stream: "delta" events, then one
                                         "done" event carrying the full output
    POST /workflows/{name}           -> JSON state in, final state out
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

from agentdeck.app import App
from agentdeck.workflows.state import json_default

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def create_app() -> Any:
    from fastapi import FastAPI
    from fastapi.responses import StreamingResponse

    deck = App()
    inventory = deck.load()
    api = FastAPI(title="agentdeck")

    @api.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", **inventory}

    @api.post("/agents/{name}/chat")
    async def chat(name: str, body: dict[str, Any], stream: bool = False) -> Any:
        if stream:

            async def events() -> AsyncIterator[str]:
                # The done event's "output" is the deltas re-joined rather than a
                # separate RunResult field: for a plain-text agent (the streaming use
                # case) that's exactly the final output, and it avoids holding the SDK's
                # RunResultStreaming open past what chat_stream's contract exposes.
                chunks: list[str] = []
                async for delta in deck.chat_stream(name, body["session_id"], body["message"]):
                    chunks.append(delta)
                    yield f"data: {json.dumps({'delta': delta})}\n\n"
                yield f"event: done\ndata: {json.dumps({'output': ''.join(chunks)})}\n\n"

            return StreamingResponse(events(), media_type="text/event-stream")
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
