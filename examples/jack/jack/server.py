"""The HTTP route the docs panel talks to.

    uvicorn jack.server:app --port 8100

One route over :meth:`agentdeck.Deck.stream`, and the wire is the canonical event log  -  each
frame is one ``Event``, dumped as it was written. No translation layer, because there is nothing
to translate to: a browser switching on ``event.kind`` is reading exactly what a later process
reading the run back would read.

**Why this is not ``Deck.asgi()``.** agentdeck packages an HTTP surface and this application
cannot use it, for two independent reasons that are the point of #219 rather than an accident:

1. A run started through ``asgi()`` carries ``context=None``. There is no wire form for a live
   Python object, so the packaged surface cannot deliver one  -  and both of this agent's tools
   need the ``DocsCorpus``.
2. Its chat body is exactly ``{"session_id", "message"}``, and that wire is frozen byte-for-byte
   by ``tests/golden/``. Page context has nowhere to go in it, and widening it is a schema change
   this issue may not make.

So a real embedded application writes its own route. That is a finding about the surface, not a
complaint about it  -  forty lines is a fair price, and the alternative would have been a wire
change to suit one consumer.
"""

from __future__ import annotations

import os
import re
from collections import Counter, defaultdict
from contextlib import asynccontextmanager
from http import HTTPStatus
from time import monotonic
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agentdeck import Deck
from agentdeck.runtime.settings import get_settings
from jack.agent import jack
from jack.corpus import DocsCorpus

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from agentdeck.core.ports import EventSinkPort

AGENT = "Jack"

# The docs site is a static bundle on another origin  -  GitHub Pages in production, :3030 in
# `npm run dev`  -  so the browser will not call this without CORS. Configurable because the
# production origin changes when the site does, and a hardcoded host would outlive it.
#
# CORS is not a security control: it constrains browsers and nothing else, and a `curl` ignores
# it entirely. The controls that matter for a publicly reachable endpoint are below.
ALLOWED_ORIGINS = os.environ.get("JACK_ORIGINS", "http://localhost:3030,http://127.0.0.1:3030").split(",")

# Only these reach the browser. The stream is still canonical events  -  no reshaping, no
# translation layer  -  but it is an allowlist rather than everything the run emits, because this
# endpoint is reachable by anyone who learns the hostname:
#
# - `tool.call.completed` carries `result_preview`, which is `str(tool_output)` verbatim. These
#   two tools return documentation, which is public  -  but a tool that *raised* would put its
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

SESSIONS_PER_DAY = int(os.environ.get("JACK_SESSIONS_PER_DAY", "3"))
TURNS_PER_SESSION = int(os.environ.get("JACK_TURNS_PER_SESSION", "20"))
DAY = 86_400.0
"""The whole quota: three conversations a client may start in a day, twenty turns in each.

Sixty turns per client per day is the hard ceiling on what this endpoint can be made to spend,
and it is deliberately shaped as *conversations* rather than as a flat request count, because
the two limits stop different things:

- **Turns per session** bound the conversation. A session re-sends its whole history to the
  model on every turn, so an unbounded one costs quadratically while its context window fills
  with a caller's own text  -  that is how you overload this without ever sending a long message.
- **Sessions per day** stop the obvious way around the first: starting a fresh conversation
  every twenty turns and carrying on.

Generous for reading documentation, useless as a free model. The endpoint is unauthenticated on
purpose  -  a docs assistant that asks you to log in is not a docs assistant  -  so this is the
whole of what stands between a public hostname and someone else's bill.
"""


class Question(BaseModel):
    """What the panel sends. Everything but the question is optional: the assistant has to work
    on a page that exposes no selection, and from a `curl` that knows about no page at all.

    Every field is length-capped. Validation at a trust boundary is not the place to be lazy,
    and `selection` in particular is whatever a caller says the reader highlighted  -  uncapped, it
    is an arbitrary-length prompt injected straight into a model call someone else pays for.
    """

    question: str = Field(min_length=1, max_length=MAX_QUESTION)
    page: str | None = Field(default=None, max_length=200)
    """The slug of the page the reader is on, e.g. `reference/deck`  -  the site's own slug."""
    selection: str | None = Field(default=None, max_length=MAX_QUESTION)
    """Whatever the reader had selected, if the UI exposes it."""
    session_id: str | None = Field(default=None, max_length=100)


def page_context_input(asked: Question, corpus: DocsCorpus | None = None) -> str:
    """The question, with what the reader is looking at prefixed as text.

    **Both prefixed fields are attacker-controlled**, because the browser is. They are prompt
    input, so they get treated as such:

    - `page` is checked against the corpus and dropped if it names no real page. A slug that is
      not a slug is meaningless anyway, which makes this both the security fix and the correct
      behaviour  -  and it removes the field as an injection vector completely, since the only
      values that survive are 22 known strings.
    - `selection` cannot be validated that way; it is arbitrary text by definition. So the
      delimiter is stripped out of it. Without that, a `selection` containing `</context>`
      closes the block early and everything after it reads to the model as instructions rather
      than as quoted material  -  the ordinary way a delimiter-based preamble is broken.

    Neither makes the model *obey* only what it should; see this example's README on what is and
    is not guarded. They stop the structure of the prompt being forged, which is the part that
    can actually be fixed here.

    **Text, not a `DataBlock`, and not a `Context`.** Both of the tidier-looking options are
    closed:

    - A `Context[T]` cannot cross HTTP at all. That is the documented boundary, and it is the
      right one  -  the page slug is data a browser sent, not a live object this server owns.
    - `DataBlock` is the typed way to put JSON in an input, and the openai-agents engine refuses
      it: *"cannot send a 'data' block to the model; it accepts text, image, and audio"*. It is
      an output block in practice.

    So the preamble is prose, delimited so the model can tell it from the question. That is not a
    workaround  -  a page slug is something the model reads, and reading is what text is for  -  but
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


def observers() -> list[EventSinkPort]:
    """Langfuse when it is configured, nothing when it is not.

    A public endpoint that keeps no record of itself cannot be debugged after a complaint and
    cannot be audited at all, and this one is unauthenticated. But a reader who clones the example
    should not need a tracing backend to run it, so the keys decide: set
    ``AGENTDECK_LANGFUSE_PUBLIC_KEY`` and runs are traced, leave it unset and nothing is sent.

    Traces carry what visitors typed. Point this at an instance you control.
    """
    if not get_settings().langfuse.public_key:
        return []
    from agentdeck.observers import Langfuse

    return [Langfuse()]


def build_app(corpus: DocsCorpus | None = None) -> FastAPI:
    """The whole surface. Takes the corpus so a test can point it at a fixture directory."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """One deck for the process, opened before the first request and closed after the last  -
        which is also all one process may have (#204). Building it here rather than per request
        is not an optimisation: `build()` compiles the catalog and checks every `Context[...]`
        against the declared type, and that should fail at startup, not on someone's question.
        """
        resolved = corpus or DocsCorpus()
        async with Deck(agents=[jack], context=DocsCorpus, observers=observers()) as deck:
            app.state.deck, app.state.corpus = deck, resolved
            app.state.quota = Quota(SESSIONS_PER_DAY, TURNS_PER_SESSION)
            yield

    app = FastAPI(title="Jack", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_methods=["POST"], allow_headers=["content-type"]
    )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "pages": len(app.state.corpus.pages)}

    @app.post("/ask")
    async def answer(asked: Question, request: Request) -> StreamingResponse:
        origin = request.headers.get("origin")
        if origin not in ALLOWED_ORIGINS:
            # Enforced here, not left to CORS. CORSMiddleware only tells a *browser* not to hand
            # the response back; the run has already happened and been paid for by then. This
            # refuses before the model is called, which is the difference that matters.
            #
            # It is not authentication and must not be mistaken for it: `Origin` is set by
            # browsers and forged by anything else in one flag. What it does buy is real  -
            # another website cannot embed this endpoint, and casual reuse stops  -  but a script
            # that sets the header is indistinguishable from the docs site. The quota above is
            # what bounds that case; Turnstile is what would end it. See the README.
            raise HTTPException(
                status_code=HTTPStatus.FORBIDDEN,
                detail="this assistant answers the AgentDeck documentation site",
            )
        client = request.client.host if request.client else "unknown"
        # A caller that sends no session id still gets one bucket rather than a free pass  -
        # otherwise "omit the field" is the way around the whole quota.
        refusal = app.state.quota.refuse(client, asked.session_id or "-")
        if refusal is not None:
            raise HTTPException(status_code=HTTPStatus.TOO_MANY_REQUESTS, detail=refusal)
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


class Quota:
    """Sessions per client per day, and turns per session. Both counted in this process.

    # ponytail: in-memory, per-IP, single process  -  right for one backend behind one tunnel, and
    # wrong the moment there are two replicas or a caller with addresses to spare. The upgrade is
    # Cloudflare's own rate limiting at the edge, where the traffic never reaches the machine;
    # this stays as the floor underneath it.
    """

    def __init__(self, sessions_per_day: int, turns_per_session: int, day: float = DAY) -> None:
        self._sessions_per_day, self._turns_per_session, self._day = sessions_per_day, turns_per_session, day
        self._started: dict[str, dict[str, float]] = defaultdict(dict)
        self._turns: Counter[tuple[str, str]] = Counter()

    def refuse(self, client: str, session: str) -> str | None:
        """The reason to refuse this turn, or ``None`` to allow it and count it."""
        now = monotonic()
        started = self._started[client]
        for stale in [name for name, at in started.items() if now - at > self._day]:
            del started[stale]
            del self._turns[client, stale]

        if session not in started:
            if len(started) >= self._sessions_per_day:
                return f"{self._sessions_per_day} conversations already today  -  this resets on a rolling 24 hours"
            started[session] = now

        if self._turns[client, session] >= self._turns_per_session:
            return f"this conversation has reached {self._turns_per_session} turns  -  start a new one"
        self._turns[client, session] += 1
        return None


app = build_app()

__all__ = [
    "PUBLIC_KINDS",
    "SESSIONS_PER_DAY",
    "TURNS_PER_SESSION",
    "Question",
    "Quota",
    "app",
    "build_app",
    "page_context_input",
]
