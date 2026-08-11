"""The HTTP route the docs panel talks to.

    uvicorn ask_agentdeck.server:app --port 8100

One route over :meth:`agentdeck.Deck.stream`, and the wire is the canonical event log — each
frame is one ``Event``, dumped as it was written. No translation layer, because there is nothing
to translate to: a browser switching on ``event.kind`` is reading exactly what a later process
reading the run back would read.

**Why this is not ``Deck.asgi()``.** agentdeck packages an HTTP surface and this application
cannot use it, for two independent reasons that are the point of #219 rather than an accident:

1. A run started through ``asgi()`` carries ``context=None``. There is no wire form for a live
   Python object, so the packaged surface cannot deliver one — and both of this agent's tools
   need the ``DocsCorpus``.
2. Its chat body is exactly ``{"session_id", "message"}``, and that wire is frozen byte-for-byte
   by ``tests/golden/``. Page context has nowhere to go in it, and widening it is a schema change
   this issue may not make.

So a real embedded application writes its own route. That is a finding about the surface, not a
complaint about it — forty lines is a fair price, and the alternative would have been a wire
change to suit one consumer.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agentdeck import Deck
from ask_agentdeck.agent import ask
from ask_agentdeck.corpus import DocsCorpus

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

AGENT = "AskAgentDeck"

# The docs site is a static bundle on another origin — GitHub Pages in production, :3030 in
# `npm run dev` — so the browser will not call this without CORS. Configurable because the
# production origin changes when the site does, and a hardcoded host would outlive it.
ALLOWED_ORIGINS = os.environ.get("ASK_AGENTDECK_ORIGINS", "http://localhost:3030,http://127.0.0.1:3030").split(",")


class Question(BaseModel):
    """What the panel sends. Everything but the question is optional: the assistant has to work
    on a page that exposes no selection, and from a `curl` that knows about no page at all."""

    question: str = Field(min_length=1)
    page: str | None = None
    """The slug of the page the reader is on, e.g. `reference/deck` — the site's own slug."""
    selection: str | None = None
    """Whatever the reader had selected, if the UI exposes it."""
    session_id: str | None = None


def page_context_input(asked: Question) -> str:
    """The question, with what the reader is looking at prefixed as text.

    **Text, not a `DataBlock`, and not a `Context`.** Both of the tidier-looking options are
    closed:

    - A `Context[T]` cannot cross HTTP at all. That is the documented boundary, and it is the
      right one — the page slug is data a browser sent, not a live object this server owns.
    - `DataBlock` is the typed way to put JSON in an input, and the openai-agents engine refuses
      it: *"cannot send a 'data' block to the model; it accepts text, image, and audio"*. It is
      an output block in practice.

    So the preamble is prose, delimited so the model can tell it from the question. That is not a
    workaround — a page slug is something the model reads, and reading is what text is for — but
    it does mean every embedded application invents its own preamble format. Recorded as a
    finding: whether the wire should carry a structured per-run metadata channel is a v3.1
    question, not a v3 one.
    """
    if not asked.page and not asked.selection:
        return asked.question
    lines = ["<context>"]
    if asked.page:
        lines.append(f"The reader is on the documentation page: {asked.page}")
    if asked.selection:
        lines.append(f"They have this selected:\n{asked.selection}")
    lines += ["</context>", "", asked.question]
    return "\n".join(lines)


def build_app(corpus: DocsCorpus | None = None) -> FastAPI:
    """The whole surface. Takes the corpus so a test can point it at a fixture directory."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """One deck for the process, opened before the first request and closed after the last —
        which is also all one process may have (#204). Building it here rather than per request
        is not an optimisation: `build()` compiles the catalog and checks every `Context[...]`
        against the declared type, and that should fail at startup, not on someone's question.
        """
        resolved = corpus or DocsCorpus()
        async with Deck(agents=[ask], context=DocsCorpus) as deck:
            app.state.deck, app.state.corpus = deck, resolved
            yield

    app = FastAPI(title="Ask AgentDeck", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_methods=["POST"], allow_headers=["content-type"]
    )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "pages": len(app.state.corpus.pages)}

    @app.post("/ask")
    async def answer(asked: Question) -> StreamingResponse:
        stream = app.state.deck.stream(
            AGENT,
            page_context_input(asked),
            context=app.state.corpus,
            session_id=asked.session_id,
        )

        async def frames() -> AsyncIterator[str]:
            async for event in stream:
                yield f"data: {event.model_dump_json()}\n\n"

        return StreamingResponse(frames(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    return app


app = build_app()

__all__ = ["Question", "app", "build_app", "page_context_input"]
