"""One crude SSE route for v2 runs (#52, M0 step 3) — separate from v1's ``serve.py``,
which this PR does not touch. No auth, no discovery: the composition root hands in an
already-wired ``Runtime`` (see ``docs/delivery/milestone-0-walking-skeleton.md`` §1,
"fake shamelessly"). Skeleton component: hardened or discarded at the M0 review, not
polished now.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agentdeck.core.content import coerce_input
from agentdeck.core.context import RunContext

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from agentdeck.runtime.service import Runtime

# M0 fakes auth away entirely (milestone doc §1) — every run shares one tenant/principal.
TENANT = "demo"
PRINCIPAL = "user:demo"


class ChatBody(BaseModel):
    """Validated at the trust boundary (coding-standards.md §12) instead of a bare
    ``body["session_id"]`` — a missing field is a 422 from FastAPI, not a 500 mid-stream."""

    session_id: str
    message: str


def build_app(runtime: Runtime) -> FastAPI:
    """The whole surface: one route, streaming ``Event.model_dump_json()`` lines.

    No ``_jsonable`` reshaping like v1's ``serve.py`` — the wire *is* the canonical event,
    which is the point of having one.
    """
    api = FastAPI()

    @api.post("/v2/invocables/{name}/chat")
    async def chat(name: str, body: ChatBody) -> Any:
        ctx = RunContext(
            tenant=TENANT,
            principal=PRINCIPAL,
            run_id=str(uuid.uuid4()),
            trace_id=str(uuid.uuid4()),
            session_id=body.session_id,
        )

        async def frames() -> AsyncIterator[str]:
            async for event in runtime.run(name, coerce_input(body.message), ctx):
                yield f"data: {event.model_dump_json()}\n\n"

        return StreamingResponse(frames(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    return api


__all__ = ["build_app"]
