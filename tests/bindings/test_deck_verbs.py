"""``Deck.asgi``/``Deck.serve_async`` are one-line delegations to ``expose(*bindings).asgi()``/
``.serve()`` (#606); ``Deck.serve`` (#623) is the synchronous form, ``asyncio.run`` over
``serve_async``. ``agentdeck.bindings`` lazily re-exports the in-tree binding factories.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest
from starlette.testclient import TestClient

from agentdeck.authoring import Agent
from agentdeck.bindings import BindingInfo, StdioEndpoint
from agentdeck.bindings.native import Native
from agentdeck.deck import Deck
from agentdeck.errors import ConfigError


def _deck() -> Deck:
    return Deck(agents=[Agent(name="Greeter", instructions="Greet the user.")])


class _RaisingBinding:
    """A binding whose ``start()`` always fails, before any listener or stdio task exists."""

    info = BindingInfo(name="raiser", kind="surface", transport="stdio", spi_version=1, advertises=frozenset())

    def build(self, gateway: object) -> StdioEndpoint:
        async def run() -> None:
            raise AssertionError("never reached: start() raises first")

        return StdioEndpoint(run=run)

    async def start(self) -> None:
        raise RuntimeError("boom: this binding cannot start")

    async def stop(self) -> None:
        pass


def test_asgi_delegates_to_expose_with_the_exact_bindings_given(monkeypatch) -> None:
    calls: list[tuple[object, ...]] = []

    class FakeExposure:
        def asgi(self) -> str:
            return "sentinel-app"

    def fake_expose(self: Deck, *bindings: object) -> FakeExposure:
        calls.append(bindings)
        return FakeExposure()

    monkeypatch.setattr(Deck, "expose", fake_expose)
    native = Native.http()

    app = _deck().asgi(native)

    assert calls == [(native,)]
    assert app == "sentinel-app"


async def test_serve_async_delegates_to_expose_with_the_exact_bindings_and_host_port(monkeypatch) -> None:
    calls: list[tuple[object, ...]] = []
    seen = {}

    class FakeExposure:
        async def serve(self, *, host: str, port: int) -> None:
            seen["host"] = host
            seen["port"] = port

    def fake_expose(self: Deck, *bindings: object) -> FakeExposure:
        calls.append(bindings)
        return FakeExposure()

    monkeypatch.setattr(Deck, "expose", fake_expose)
    native = Native.http()

    await _deck().serve_async(native, host="127.0.0.1", port=9001)

    assert calls == [(native,)]
    assert seen == {"host": "127.0.0.1", "port": 9001}


def test_serve_delegates_to_serve_async_with_the_exact_bindings_host_and_port(monkeypatch) -> None:
    seen = {}

    async def fake_serve_async(self: Deck, *bindings: object, host: str, port: int) -> None:
        seen["bindings"] = bindings
        seen["host"] = host
        seen["port"] = port

    monkeypatch.setattr(Deck, "serve_async", fake_serve_async)
    native = Native.http()

    _deck().serve(native, host="127.0.0.1", port=9002)

    assert seen == {"bindings": (native,), "host": "127.0.0.1", "port": 9002}


async def test_serve_inside_a_running_loop_raises_a_config_error_naming_serve_async() -> None:
    with pytest.raises(ConfigError, match=r"await deck\.serve_async\(\.\.\.\)"):
        _deck().serve(Native.http())


def test_asgi_with_no_binding_raises_type_error() -> None:
    with pytest.raises(TypeError):
        _deck().asgi()


def test_serve_with_no_binding_raises_type_error() -> None:
    with pytest.raises(TypeError):
        _deck().serve()


async def test_serve_async_serves_native_http_end_to_end_over_a_real_request(monkeypatch) -> None:
    """A real ASGI request through ``Native.http()``, mounted the way ``serve_async`` mounts it.
    Uvicorn itself is faked (as in ``test_bindings_exposure.py``): ``Exposure.serve()`` builds its
    ``uvicorn.Server`` locally with no returned handle or ``should_exit`` hook, so there is no way
    to shut a real bound socket down cleanly from outside it (see the PR design)."""
    deck = _deck()
    served: list[object] = []

    class _FakeServer:
        def __init__(self, config: tuple[object, str, int]) -> None:
            self._config = config

        async def serve(self) -> None:
            served.append(self._config)

    fake_uvicorn = type(
        "_FakeUvicorn",
        (),
        {"Config": staticmethod(lambda app, host, port: (app, host, port)), "Server": _FakeServer},
    )
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)

    await deck.serve_async(Native.http(), host="127.0.0.1", port=9101)

    assert len(served) == 1
    app, host, port = served[0]
    assert (host, port) == ("127.0.0.1", 9101)
    with TestClient(app) as client:
        response = client.get("/targets")
    assert response.status_code == 200
    assert {t["name"] for t in response.json()} == {"Greeter"}
    assert not deck.is_open


def test_serve_propagates_a_startup_exception() -> None:
    with pytest.raises(RuntimeError, match="boom"):
        _deck().serve(_RaisingBinding())


def test_serve_swallows_keyboard_interrupt_like_uvicorn_run(monkeypatch) -> None:
    """Matches the cited analog, `uvicorn.run`: Ctrl-C stops the server and returns quietly,
    rather than a raw traceback reaching a caller of the blocking entrypoint."""

    async def fake_serve_async(self: Deck, *bindings: object, host: str, port: int) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(Deck, "serve_async", fake_serve_async)

    assert _deck().serve(Native.http()) is None


def test_lazy_import_from_agentdeck_bindings() -> None:
    from agentdeck.bindings import AGUI as LazyAGUI  # noqa: N811
    from agentdeck.bindings import Native as LazyNative
    from agentdeck.bindings import Terminal as LazyTerminal

    assert LazyNative is Native
    from agentdeck.bindings.terminal import Terminal

    assert LazyTerminal is Terminal
    from agentdeck.bindings.agui import AGUI

    assert LazyAGUI is AGUI


def test_an_unknown_lazy_name_names_the_available_factories() -> None:
    import agentdeck.bindings as bindings

    with pytest.raises(AttributeError) as excinfo:
        bindings.Bogus  # noqa: B018

    message = str(excinfo.value)
    assert "AGUI" in message
    assert "Native" in message
    assert "Terminal" in message


def test_dir_lists_the_lazy_names_too() -> None:
    import agentdeck.bindings as bindings

    assert {"AGUI", "Native", "Terminal"} <= set(dir(bindings))


def test_importing_bindings_pulls_in_no_binding_module_or_new_starlette_module() -> None:
    """``agentdeck`` itself already imports the openai-agents SDK's ``agents.mcp``, which loads
    ``starlette`` transitively (a base dependency, not a binding); the claim under test is that
    ``agentdeck.bindings`` adds nothing on top of that, until a factory is actually named -
    including AG-UI's own SDK, which no other binding pulls in at all.
    """
    probe = textwrap.dedent("""
        import sys
        import agentdeck
        before = {m for m in sys.modules if m.startswith("starlette")}
        import agentdeck.bindings
        after = {m for m in sys.modules if m.startswith("starlette")}
        assert after == before, after - before
        assert not [m for m in sys.modules if m.startswith("agentdeck.adapters.bindings")]
        assert not [m for m in sys.modules if m.startswith("ag_ui")]
        """)
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
