"""``Deck.serve``/``Deck.asgi`` are one-line delegations to ``expose(*bindings).serve()``/
``.asgi()`` (#606); ``agentdeck.bindings`` lazily re-exports the in-tree binding factories.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

from agentdeck.authoring import Agent
from agentdeck.bindings.native import Native
from agentdeck.deck import Deck


def _deck() -> Deck:
    return Deck(agents=[Agent(name="Greeter", instructions="Greet the user.")])


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


async def test_serve_delegates_to_expose_with_the_exact_bindings_and_host_port(monkeypatch) -> None:
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

    await _deck().serve(native, host="127.0.0.1", port=9001)

    assert calls == [(native,)]
    assert seen == {"host": "127.0.0.1", "port": 9001}


def test_asgi_with_no_binding_raises_type_error() -> None:
    with pytest.raises(TypeError):
        _deck().asgi()


async def test_serve_with_no_binding_raises_type_error() -> None:
    with pytest.raises(TypeError):
        await _deck().serve()


def test_lazy_import_from_agentdeck_bindings() -> None:
    from agentdeck.bindings import Native as LazyNative
    from agentdeck.bindings import Terminal as LazyTerminal

    assert LazyNative is Native
    from agentdeck.bindings.terminal import Terminal

    assert LazyTerminal is Terminal


def test_an_unknown_lazy_name_names_the_available_factories() -> None:
    import agentdeck.bindings as bindings

    with pytest.raises(AttributeError, match="Native.*Terminal|Terminal.*Native"):
        bindings.Bogus  # noqa: B018


def test_dir_lists_the_lazy_names_too() -> None:
    import agentdeck.bindings as bindings

    assert {"Native", "Terminal"} <= set(dir(bindings))


def test_importing_bindings_pulls_in_no_binding_module_or_new_starlette_module() -> None:
    """``agentdeck`` itself already imports the openai-agents SDK's ``agents.mcp``, which loads
    ``starlette`` transitively (a base dependency, not a binding); the claim under test is that
    ``agentdeck.bindings`` adds nothing on top of that, until a factory is actually named.
    """
    probe = textwrap.dedent("""
        import sys
        import agentdeck
        before = {m for m in sys.modules if m.startswith("starlette")}
        import agentdeck.bindings
        after = {m for m in sys.modules if m.startswith("starlette")}
        assert after == before, after - before
        assert not [m for m in sys.modules if m.startswith("agentdeck.adapters.bindings")]
        """)
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
