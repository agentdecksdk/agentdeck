"""Minimal HTTP surface: serve the ./.agentdeck project over FastAPI.

    agentdeck-serve                  # console script; HOST / PORT env override

Endpoints:
    GET  /health                     -> {"status": "ok", agents, workflows, skills}
    POST /agents/{name}/chat         -> {"session_id", "message"} -> {"output"}
    POST /agents/{name}/chat?stream=true
                                      -> text/event-stream: "delta" events, then one
                                         "done" event carrying {"output", "usage"};
                                         an "error" event replaces "done" if the turn fails
    POST /workflows/{name}           -> JSON state in, final state out
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

from agentdeck.agents.runners import StreamDone
from agentdeck.app import App
from agentdeck.workflows.state import json_default

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def create_app() -> Any:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import StreamingResponse

    deck = App()
    inventory = deck.load()
    api = FastAPI(title="agentdeck")

    @api.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", **inventory}

    @api.post("/agents/{name}/chat")
    async def chat(name: str, body: dict[str, Any], stream: bool = False) -> Any:
        # Read the body up front: inside the generator a KeyError would surface as a
        # 200 that just stops, since the response headers are already on the wire.
        try:
            session_id, message = body["session_id"], body["message"]
        except KeyError as exc:
            raise HTTPException(status_code=422, detail=f"missing field: {exc.args[0]}") from exc
        if stream:

            async def events() -> AsyncIterator[str]:
                try:
                    async for chunk in deck.chat_stream(name, session_id, message):
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
        result = await deck.chat(name, session_id, message)
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
