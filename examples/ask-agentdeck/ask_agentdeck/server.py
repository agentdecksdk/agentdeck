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
import re
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from http import HTTPStatus
from time import monotonic
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException, Request
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
#
# CORS is not a security control: it constrains browsers and nothing else, and a `curl` ignores
# it entirely. The controls that matter for a publicly reachable endpoint are below.
ALLOWED_ORIGINS = os.environ.get("ASK_AGENTDECK_ORIGINS", "http://localhost:3030,http://127.0.0.1:3030").split(",")

# Only these reach the browser. The stream is still canonical events — no reshaping, no
# translation layer — but it is an allowlist rather than everything the run emits, because this
# endpoint is reachable by anyone who learns the hostname:
#
# - `tool.call.completed` carries `result_preview`, which is `str(tool_output)` verbatim. These
#   two tools return documentation, which is public — but a tool that *raised* would put its
#   exception text there, and that is the one channel on this wire that could carry an internal
#   detail outward. Dropped, and the panel never needed it: `tool.call.started` already says
#   which page is being read.
# - `usage.reported` names the model and the token count of every turn. Not a secret, and not
#   anonymous callers' business either.
#
# `run.failed` stays, and is safe by agentdeck's own design: its `message` is the exception's
# *type name* and the engine's, never the exception text (`runtime/service.py:655`).
PUBLIC_KINDS = frozenset({"run.started", "text.delta", "tool.call.started", "run.completed", "run.failed"})

_DELIMITER = re.compile(r"</?context>", re.IGNORECASE)
"""The preamble's own tags, removed from anything a caller supplied."""

MAX_QUESTION = 2000
"""A docs question is a sentence. The cap is what stops this being a free prompt-relay to
whatever model the deck is pointed at."""

RATE_LIMIT = int(os.environ.get("ASK_AGENTDECK_RATE_LIMIT", "20"))
RATE_WINDOW = 300.0
"""Questions per client per five minutes. The endpoint is unauthenticated on purpose — a docs
assistant that asks you to log in is not a docs assistant — so this is what stands between the
hostname and someone else's model bill. Deliberately generous for a reader, useless for a script.

# ponytail: in-process and per-IP, which is right for one process behind one tunnel. A second
# replica, or an attacker with addresses to spare, needs Cloudflare's own rate limiting at the
# edge — see this example's README.
"""


class Question(BaseModel):
    """What the panel sends. Everything but the question is optional: the assistant has to work
    on a page that exposes no selection, and from a `curl` that knows about no page at all.

    Every field is length-capped. Validation at a trust boundary is not the place to be lazy,
    and `selection` in particular is whatever a caller says the reader highlighted — uncapped, it
    is an arbitrary-length prompt injected straight into a model call someone else pays for.
    """

    question: str = Field(min_length=1, max_length=MAX_QUESTION)
    page: str | None = Field(default=None, max_length=200)
    """The slug of the page the reader is on, e.g. `reference/deck` — the site's own slug."""
    selection: str | None = Field(default=None, max_length=MAX_QUESTION)
    """Whatever the reader had selected, if the UI exposes it."""
    session_id: str | None = Field(default=None, max_length=100)


def page_context_input(asked: Question, corpus: DocsCorpus | None = None) -> str:
    """The question, with what the reader is looking at prefixed as text.

    **Both prefixed fields are attacker-controlled**, because the browser is. They are prompt
    input, so they get treated as such:

    - `page` is checked against the corpus and dropped if it names no real page. A slug that is
      not a slug is meaningless anyway, which makes this both the security fix and the correct
      behaviour — and it removes the field as an injection vector completely, since the only
      values that survive are 22 known strings.
    - `selection` cannot be validated that way; it is arbitrary text by definition. So the
      delimiter is stripped out of it. Without that, a `selection` containing `</context>`
      closes the block early and everything after it reads to the model as instructions rather
      than as quoted material — the ordinary way a delimiter-based preamble is broken.

    Neither makes the model *obey* only what it should; see this example's README on what is and
    is not guarded. They stop the structure of the prompt being forged, which is the part that
    can actually be fixed here.

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
    page = asked.page if corpus is not None and asked.page in corpus.pages else None
    selection = _DELIMITER.sub("", asked.selection) if asked.selection else None
    if not page and not selection:
        return asked.question
    lines = ["<context>"]
    if page:
        lines.append(f"The reader is on the documentation page: {page}")
    if selection:
        lines.append(f"They have this selected:\n{selection}")
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
            app.state.limiter = RateLimiter(RATE_LIMIT, RATE_WINDOW)
            yield

    app = FastAPI(title="Ask AgentDeck", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_methods=["POST"], allow_headers=["content-type"]
    )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "pages": len(app.state.corpus.pages)}

    @app.post("/ask")
    async def answer(asked: Question, request: Request) -> StreamingResponse:
        if not app.state.limiter.allow(request.client.host if request.client else "unknown"):
            raise HTTPException(
                status_code=HTTPStatus.TOO_MANY_REQUESTS,
                detail=f"more than {RATE_LIMIT} questions in {int(RATE_WINDOW / 60)} minutes — try again shortly",
            )
        stream = app.state.deck.stream(
            AGENT,
            page_context_input(asked, app.state.corpus),
            context=app.state.corpus,
            session_id=asked.session_id,
        )

        async def frames() -> AsyncIterator[str]:
            async for event in stream:
                if event.kind in PUBLIC_KINDS:
                    yield f"data: {event.model_dump_json()}\n\n"

        return StreamingResponse(frames(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    return app


class RateLimiter:
    """One deque of timestamps per client, trimmed to the window on each look."""

    def __init__(self, limit: int, window: float) -> None:
        self._limit, self._window, self._seen = limit, window, defaultdict(deque)

    def allow(self, client: str) -> bool:
        now = monotonic()
        seen: deque[float] = self._seen[client]
        while seen and now - seen[0] > self._window:
            seen.popleft()
        if len(seen) >= self._limit:
            return False
        seen.append(now)
        return True


app = build_app()

__all__ = ["PUBLIC_KINDS", "Question", "RateLimiter", "app", "build_app", "page_context_input"]
