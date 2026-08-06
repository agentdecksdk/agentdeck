"""Minimal HTTP surface: serve the ./.agentdeck project over FastAPI.

    agentdeck-serve                  # console script; HOST / PORT env override

The chat endpoints run on the v2 ``Runtime`` that ``App`` composes: the handler builds a
``RunContext``, calls ``Runtime.run``, and hands the canonical events to
``surfaces/serve/compat.py``, which renders v1's frames. The workflow endpoints still run
on v1's workflow runner. The wire below is unchanged either way — that is the point, and
``tests/golden/`` is what proves it.

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
    POST /workflows/{name}?stream=true
                                      -> text/event-stream: "node_update"/"custom" events per
                                         the LangGraph node updates/custom stream, then one
                                         "done" event carrying the final state — or one
                                         "interrupt" event in its place when the run pauses;
                                         an "error" event replaces either if the run fails
    GET  /workflows/{name}/pending   -> [{"type": "interrupt", "payload", "thread_id"}, ...]
    POST /workflows/{name}/{thread_id}/resume
                                      -> {"value": ...} -> final state, or the next interrupt
    POST /runs/{run_id}/pause        -> {"reason": ...}? -> {"run_id", "verb", "recorded": true}
    POST /runs/{run_id}/cancel       -> {"reason": ...}? -> {"run_id", "verb", "recorded": true}
                                        Recorded, not applied: the run stops at its next safe
                                        point, and its own run.paused / run.cancelled event is
                                        what says it did
    POST /runs/{run_id}/resume       -> {"reason": ...}? -> {"run_id", "status", "events"} for
                                        the continuation this call played — 409 if the run is
                                        not paused
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from agentdeck.app import App
from agentdeck.core.content import coerce_input
from agentdeck.core.status import status_of
from agentdeck.errors import AgentdeckError, NotFoundError
from agentdeck.surfaces.serve.compat import chat_frames, chat_result, run_context
from agentdeck.workflows.state import json_default

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

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
            _warn_if_event_log_is_in_memory(deck)
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
        # Read and validate the body up front: inside the generator either failure would
        # surface as a 200 that just stops, since the response headers are already on the wire.
        try:
            session_id, message = body["session_id"], body["message"]
        except KeyError as exc:
            raise HTTPException(status_code=422, detail=f"missing field: {exc.args[0]}") from exc
        # A body shape this endpoint cannot run is the client's mistake, not a server fault: 4xx
        # like every other bad body here, never a 500 in somebody's alerting. Both fields are
        # checked, because both reach a model that only accepts a string.
        if not isinstance(session_id, str):
            raise HTTPException(status_code=422, detail=f"session_id must be a string, got {type(session_id).__name__}")
        try:
            content = coerce_input(message)
        except TypeError as exc:
            raise HTTPException(
                status_code=422, detail=f"message must be a string, got {type(message).__name__}"
            ) from exc
        app = deck()  # resolve before streaming so a pre-startup 503 keeps its status code
        # Resolved against the agent registry, not the Runtime's invocables: this route is
        # agents-only (a workflow name must still 404 here), and the registry's message is
        # the one v1 answers with.
        app.agents.get(name)
        run = app.runtime.run(name, content, run_context(session_id))
        if stream:
            return StreamingResponse(
                chat_frames(run),
                media_type="text/event-stream",
                # Proxies buffer streamed responses by default; nginx needs X-Accel-Buffering.
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        return await chat_result(run)

    @api.post("/workflows/{name}")
    async def run_workflow(name: str, state: dict[str, Any], stream: bool = False, thread_id: str | None = None) -> Any:
        app = deck()  # resolve before streaming so a pre-startup 503 keeps its status code
        if stream:

            async def events() -> AsyncIterator[str]:
                try:
                    async for event in app.run_workflow_stream(name, state, thread_id=thread_id):
                        if event["type"] == "done":
                            yield f"event: done\ndata: {json.dumps(event['state'], default=json_default)}\n\n"
                        elif event["type"] == "interrupt":
                            yield f"event: interrupt\ndata: {json.dumps(event, default=json_default)}\n\n"
                        else:
                            yield f"data: {json.dumps(event, default=json_default)}\n\n"
                except Exception as exc:
                    # Mid-stream failures can't change the status code any more; report
                    # them in-band without leaking internals.
                    yield f"event: error\ndata: {json.dumps({'error': type(exc).__name__})}\n\n"

            return StreamingResponse(
                events(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        return _jsonable(await app.run_workflow(name, state, thread_id=thread_id))

    @api.post("/runs/{run_id}/pause")
    async def pause_run(run_id: str, body: dict[str, Any] | None = None) -> Any:
        return await _control(deck().pause_run, run_id, body, "pause")

    @api.post("/runs/{run_id}/cancel")
    async def cancel_run(run_id: str, body: dict[str, Any] | None = None) -> Any:
        return await _control(deck().cancel_run, run_id, body, "cancel")

    @api.post("/runs/{run_id}/resume")
    async def resume_run(run_id: str, body: dict[str, Any] | None = None) -> Any:
        events = await deck().resume_run(run_id, _reason(body))
        if not events:
            # 409, not 404: the run may well exist and simply not be paused — running,
            # finished, cancelled, or already picked up by another worker. All of those are the
            # same answer to this request, and none of them is an error the caller should retry.
            raise HTTPException(status_code=409, detail=f"run {run_id!r} is not paused")
        return {"run_id": run_id, "status": status_of(events).value, "events": len(events)}

    def _reason(body: dict[str, Any] | None) -> str | None:
        """The optional ``reason`` a control request carries, validated at the trust boundary:
        it is recorded in the log and read by whoever asks why a run stopped."""
        reason = (body or {}).get("reason")
        if reason is not None and not isinstance(reason, str):
            raise HTTPException(status_code=422, detail=f"reason must be a string, got {type(reason).__name__}")
        return reason

    async def _control(
        operation: Callable[[str, str | None], Awaitable[bool]], run_id: str, body: dict[str, Any] | None, verb: str
    ) -> Any:
        """Pause and cancel are the same request: record intent for a ``run_id``, answer at once.

        ``recorded`` says the request is written down, never that the run has stopped — a run
        inside a tool call stops at its next safe point, and the run's own ``run.paused`` /
        ``run.cancelled`` event is what reports that. Runs that already ended are accepted and
        do nothing, which is what makes a double-clicked cancel harmless.
        """
        if not await operation(run_id, _reason(body)):
            raise HTTPException(status_code=503, detail="run control is unavailable: no control backend is configured")
        return {"run_id": run_id, "verb": verb, "recorded": True}

    @api.get("/workflows/{name}/pending")
    async def pending_interrupts(name: str) -> Any:
        return _jsonable(await deck().pending_interrupts(name))

    @api.post("/workflows/{name}/{thread_id}/resume")
    async def resume_workflow(name: str, thread_id: str, body: dict[str, Any]) -> Any:
        if "value" not in body:
            raise HTTPException(status_code=422, detail="missing field: value")
        return _jsonable(await deck().resume_workflow(name, thread_id, body["value"]))

    return api


def _warn_if_event_log_is_in_memory(deck: App) -> None:
    """Say so once at startup: the default event store is fine for a session and wrong for a
    server. It never evicts, every v1 request shares one tenant, and a run reads its whole
    log before it starts — so one long conversation costs quadratic reads and the process
    keeps every event it ever saw. A warning, not a refusal: dev servers want this default.
    """
    if deck.settings.events.backend.strip().lower() == "memory":
        logger.warning(
            "event log backend is 'memory': it never evicts and is lost on restart. "
            "Set AGENTDECK_EVENTS_BACKEND=sqlite and AGENTDECK_EVENTS_URL=<file> for a durable log, "
            "or redis/postgres for one several workers can share."
        )


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=json_default))


def main() -> None:
    import uvicorn

    uvicorn.run(create_app(), host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "8000")))


if __name__ == "__main__":
    main()
