"""Minimal HTTP surface: serve a project over FastAPI.

    agentdeck-serve                  # console script; HOST / PORT env override

Every endpoint below runs on the v2 ``Runtime`` a ``Deck`` composes: the handler builds a
``RunContext``, calls ``Runtime.run`` / ``Runtime.resume`` / ``Runtime.pending``, and hands the
canonical events to ``surfaces/serve/compat.py``, which renders v1's frames. So a turn — chat
or workflow — leaves one canonical event log behind. The wire below is unchanged by that; that
is the point, and ``tests/golden/`` is what proves it.

``create_app()`` is ``Deck.from_project().asgi()`` — kept as a module-level function because the
console script and every existing test import it by name; ``build_asgi_app(deck)`` is what
:meth:`agentdeck.deck.Deck.asgi` actually calls, for a ``Deck`` built any other way.

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
                                        run pauses on a human decision;
                                        409 while that thread's previous turn is unfinished,
                                        an unanswered approval included
    POST /workflows/{name}?stream=true
                                      -> text/event-stream: "node_update"/"custom" events per
                                         the LangGraph node updates/custom stream, then one
                                         "done" event carrying the final state — or one
                                         "interrupt" event in its place when the run pauses;
                                         an "error" event replaces either if the run fails
    GET  /workflows/{name}/pending   -> [{"type": "interrupt", "payload", "thread_id"}, ...]
    POST /workflows/{name}/{thread_id}/resume
                                      -> {"value": ...} -> final state, or the next interrupt;
                                         404 when the thread has no paused run to answer
    POST /runs/{run_id}/pause        -> {"reason": ...}? -> {"run_id", "verb", "recorded": true}
    POST /runs/{run_id}/cancel       -> {"reason": ...}? -> {"run_id", "verb", "recorded": true}
                                        Recorded, not applied: the run stops at its next safe
                                        point, and its own run.paused / run.cancelled event is
                                        what says it did
    POST /runs/{run_id}/resume       -> {"reason": ...}? -> {"run_id", "status", "events"} for
                                        the continuation this call played — 409 if the run is
                                        not paused

The workflow inbox above reads the event log, while ``Deck._due_resumes()`` still reads
the graph's checkpointer — so the two disagree once approvals are driven through both doors
(see the CHANGELOG for the plan to join them).
"""

from __future__ import annotations

import argparse
import logging
import os
from contextlib import aclosing, asynccontextmanager
from typing import TYPE_CHECKING, Any

from agentdeck.core.content import coerce_input
from agentdeck.core.status import status_of
from agentdeck.errors import AgentdeckError, NotFoundError, SessionBusyError
from agentdeck.surfaces.serve.compat import (
    chat_frames,
    chat_result,
    interrupt_inbox,
    workflow_frames,
    workflow_result,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable

    from fastapi import FastAPI

    from agentdeck.core.events import Event
    from agentdeck.deck import Deck

logger = logging.getLogger(__name__)


def create_app() -> Any:
    from agentdeck.deck import Deck

    return Deck.from_project().asgi()


def _require_agent(deck: Deck, name: str) -> None:
    """v1's 404 wording, owned here now that ``Deck`` exposes only the public ``agents``
    mapping — a route that needs "unknown agent" as a client-facing miss checks membership
    itself rather than reaching for a private lookup."""
    if name not in deck.agents:
        raise NotFoundError(f"No agent named {name!r}. Available: {sorted(deck.agents)}.")


def _require_workflow(deck: Deck, name: str) -> None:
    if name not in deck.workflows:
        raise NotFoundError(f"No workflow named {name!r}. Available: {sorted(deck.workflows)}.")


def build_asgi_app(deck: Deck) -> Any:
    """The FastAPI app whose lifespan opens and closes ``deck`` — what
    :meth:`agentdeck.deck.Deck.asgi` calls. Kept here, not in ``deck.py``: this is the one
    module allowed to import FastAPI and the v1 wire-rendering helpers, the same rule every
    other adapter directory follows for its own external system.
    """
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import JSONResponse, StreamingResponse

    @asynccontextmanager
    async def lifespan(api: FastAPI) -> AsyncIterator[None]:
        # `async with deck` closes the Redis session client + MCP servers on shutdown
        # (including SIGTERM from `compose stop`), so no connection is leaked. Opening it is
        # also where `resolve_event_store`/`resolve_control_port` run, which is where a
        # `memory://` backend now logs its own "won't survive a restart" warning — composition
        # time, not a server-specific check here.
        async with deck:
            api.state.deck = deck
            yield
            api.state.deck = None

    api = FastAPI(title="agentdeck", lifespan=lifespan)
    api.state.deck = None  # set by the lifespan; None means "not started yet"

    def _deck() -> Deck:
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

    # The one refusal that is neither the client's malformed input nor a server fault: the log
    # answered, and the answer was "this session already has a turn in flight". Its message
    # names the holding run, which is what a caller retries behind.
    @api.exception_handler(SessionBusyError)
    async def session_busy(_request: Request, exc: SessionBusyError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @api.exception_handler(AgentdeckError)
    async def internal_error(request: Request, exc: AgentdeckError) -> JSONResponse:
        # Every other AgentdeckError is a server-side fault, and its message can
        # carry secrets (skill stderr, config values) — log it, never ship it.
        logger.exception("%s serving %s", type(exc).__name__, request.url.path, exc_info=exc)
        return JSONResponse(status_code=500, content={"detail": "internal error"})

    # FastAPI treats the `Exception` key specially: it does not join the handlers above in
    # Starlette's per-type lookup, it becomes ServerErrorMiddleware's handler, the outermost
    # layer wrapping the whole app. That still gets this right — NotFoundError/SessionBusyError/
    # AgentdeckError are matched first, by their own registration, before anything unwinds this
    # far — and it is the only way to answer an exception the engine raised that isn't any of
    # agentdeck's own types (a workflow node's plain exception, an SDK error, an httpx transport
    # failure): unrecognized exceptions have no handler in that per-type lookup and would
    # otherwise fall through to Starlette's bare-text default.
    @api.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("%s serving %s", type(exc).__name__, request.url.path, exc_info=exc)
        return JSONResponse(status_code=500, content={"detail": "internal error"})

    @api.get("/health")
    async def health() -> Any:
        if api.state.deck is None:
            return JSONResponse({"status": "starting"}, status_code=503)
        return {"status": "ok", **_inventory(api.state.deck)}

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
        deck = _deck()  # resolve before streaming so a pre-startup 503 keeps its status code
        # Resolved against the agent catalog, not the Runtime's invocables: this route is
        # agents-only (a workflow name must still 404 here), and the message matches v1's own.
        _require_agent(deck, name)
        run = deck.stream(name, content, session_id=session_id)
        if stream:
            return StreamingResponse(
                chat_frames(await _opened(run)),
                media_type="text/event-stream",
                # Proxies buffer streamed responses by default; nginx needs X-Accel-Buffering.
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        return await chat_result(run)

    @api.post("/workflows/{name}")
    async def run_workflow(name: str, state: dict[str, Any], stream: bool = False, thread_id: str | None = None) -> Any:
        deck = _deck()  # resolve before streaming so a pre-startup 503 keeps its status code
        # Workflows-only, resolved against the workflow catalog rather than the Runtime's
        # invocables: an agent name must still 404 here, with v1's message.
        _require_workflow(deck, name)
        # The posted state *is* the graph's input, and the thread the caller named is the
        # session it runs under: one turn per thread at a time, and a resume can find it later.
        run = deck.stream(name, state, session_id=thread_id)
        if stream:
            return StreamingResponse(
                workflow_frames(await _opened(run)),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        return await workflow_result(run)

    @api.post("/runs/{run_id}/pause")
    async def pause_run(run_id: str, body: dict[str, Any] | None = None) -> Any:
        return await _control(_deck().runs.pause, run_id, body, "pause")

    @api.post("/runs/{run_id}/cancel")
    async def cancel_run(run_id: str, body: dict[str, Any] | None = None) -> Any:
        return await _control(_deck().runs.cancel, run_id, body, "cancel")

    @api.post("/runs/{run_id}/resume")
    async def resume_run(run_id: str, body: dict[str, Any] | None = None) -> Any:
        events = await _deck().runs.resume(run_id, _reason(body))
        if not events:
            # 409, not 404: the run may well exist and simply not be paused — running,
            # finished, cancelled, or already picked up by another worker. All of those are the
            # same answer to this request, and none of them is an error the caller should retry.
            raise HTTPException(status_code=409, detail=f"run {run_id!r} is not paused")
        # The events came back from a resume, so they always carry a lifecycle kind; the fallback
        # is there because ``status_of`` answers ``None`` for a sequence that carries none.
        status = status_of(events)
        return {"run_id": run_id, "status": status.value if status else None, "events": len(events)}

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
        deck = _deck()
        _require_workflow(deck, name)
        return interrupt_inbox(await deck.runs.pending(), name)

    @api.post("/workflows/{name}/{thread_id}/resume")
    async def resume_workflow(name: str, thread_id: str, body: dict[str, Any]) -> Any:
        if "value" not in body:
            raise HTTPException(status_code=422, detail="missing field: value")
        deck = _deck()
        _require_workflow(deck, name)
        # v1's own 404 names the thread, not a run_id the caller never posted — so the lookup
        # stays here rather than moving into Deck.runs.answer, whose own miss talks about run_id.
        paused = next(
            (run for run in await deck.runs.pending() if run.invocable == name and run.thread_id == thread_id),
            None,
        )
        if paused is None:
            raise NotFoundError(f"No paused run of {name!r} on thread {thread_id!r}.")
        return await deck.runs.answer(paused.run_id, body["value"])

    return api


def _inventory(deck: Deck) -> dict[str, list[str]]:
    """v1's ``{"agents": [...], "workflows": [...], "skills": [...]}`` — built from ``Deck``'s
    own properties rather than a separate cache, since they are already read-only mappings.

    ``skills.list()``, not ``.build()``: ``build()`` re-scans disk and overwrites the registry
    every call, which would make a hot probe endpoint re-read every ``SKILL.md`` on each request
    and let one edited after startup change what ``load_skill`` returns mid-run — the catalog is
    supposed to be immutable once ``BUILT``. ``Deck.build()`` already populated the cache
    ``list()`` reads.
    """
    return {
        "agents": sorted(deck.agents),
        "workflows": sorted(deck.workflows),
        "skills": sorted(deck.skills.list()) if deck.skills is not None else [],
    }


async def _opened(run: AsyncGenerator[Event, None]) -> AsyncGenerator[Event, None]:
    """``run`` with its opening event already pulled, so a refusal is still an answer.

    A run is claimed on its first event, so a generator handed straight to
    ``StreamingResponse`` has committed ``200`` and ``text/event-stream`` before the claim is
    even attempted — and a ``SessionBusyError`` then reaches the client in-band as a body that
    stops, indistinguishable from a run that produced nothing. Pulling the opening event here
    lets the refusal reach the exception handler that answers it **409**. The wire is unchanged:
    ``run.started`` renders to no v1 frame, streamed or not.
    """
    opening = await anext(run)

    async def replayed() -> AsyncGenerator[Event, None]:
        # Closing this generator does not close the one it delegates to — a consumer that walks
        # away must still land as a ``GeneratorExit`` inside the run, which is what closes it in
        # the log.
        async with aclosing(run):
            yield opening
            async for event in run:
                yield event

    return replayed()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="agentdeck-serve",
        description="Serves the deck discovered in ./.agentdeck over the v1 HTTP/SSE surface.",
        epilog="Run it from the directory that holds .agentdeck — there is no --project-dir flag.",
    )
    parser.add_argument("--host", default=os.getenv("HOST", "0.0.0.0"), help="interface to bind (env: HOST)")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")), help="port to bind (env: PORT)")
    args = parser.parse_args(argv)

    import uvicorn

    uvicorn.run(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
