"""One official-client conformance test, not deferred to AGUI-7 (#597): the real
`@ag-ui/client` (`tests/bindings/agui_client/`) driven against a live `AGUI.http()` endpoint
over a real uvicorn server in this process, proving the wire against an implementation this
binding never wrote.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any

import pytest
import uvicorn

from agentdeck import Deck, WorkflowCtx, workflow
from agentdeck.authoring import Agent
from agentdeck.bindings.agui import AGUI
from agentdeck.testing import ScriptedModel, patch_model

_AGUI_CLIENT_DIR = Path(__file__).parent / "agui_client"
_TIMEOUT = 60


async def _survey(ctx: WorkflowCtx, topic: str) -> str:
    answer = await ctx.ask(f"pick a color for {topic}?", options=["red", "blue"])
    return f"{topic}:{answer}"


def _deck() -> Deck:
    return Deck(
        agents=[Agent(name="Greeter", instructions="Greet the user.")],
        workflows=[workflow(_survey, name="Survey")],
    )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _Server:
    """A real uvicorn server for one exposure's ASGI app, run as a task in this event loop
    rather than a subprocess: the deck it serves is the test's own, patched model included.
    """

    def __init__(self, app: Any) -> None:
        self.port = _free_port()
        self._server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning"))
        self._task: asyncio.Task[None] | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/agui"

    async def __aenter__(self) -> _Server:
        self._task = asyncio.create_task(self._server.serve())
        for _ in range(200):
            if self._server.started:
                return self
            await asyncio.sleep(0.05)
        raise RuntimeError("uvicorn did not start within 10s")

    async def __aexit__(self, *exc_info: object) -> None:
        self._server.should_exit = True
        assert self._task is not None
        await self._task


def _run_scenario(url: str, scenario: str) -> dict[str, Any]:
    result = subprocess.run(
        ["node", "run_scenario.js", url, scenario],
        cwd=_AGUI_CLIENT_DIR,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def npm_install() -> None:
    if shutil.which("node") is None or shutil.which("npm") is None:
        pytest.skip("node/npm not on PATH")
    subprocess.run(["npm", "ci"], cwd=_AGUI_CLIENT_DIR, capture_output=True, text=True, timeout=_TIMEOUT, check=True)


async def test_the_official_client_completes_a_text_turn(npm_install: None) -> None:
    model = ScriptedModel(deltas=("hi",))
    with patch_model(model):
        async with _Server(_deck().expose(AGUI.http("/agui")).asgi()) as server:
            result = await asyncio.to_thread(_run_scenario, server.url, "text")

    [run] = result["runs"]
    assert run["outcome"] == "success"
    assert run["types"][0] == "RUN_STARTED"
    assert run["types"][-1] == "RUN_FINISHED"
    assert "TEXT_MESSAGE_START" in run["types"]


async def test_the_official_client_completes_a_hitl_round_trip(npm_install: None) -> None:
    async with _Server(_deck().expose(AGUI.http("/agui")).asgi()) as server:
        result = await asyncio.to_thread(_run_scenario, server.url, "hitl")

    first, resumed = result["runs"]
    assert first["outcome"] == "interrupt"
    assert resumed["outcome"] == "success"
