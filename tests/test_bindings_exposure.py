"""``Exposure``: validation at ``expose()``, then lifecycle ownership. Fake bindings only  -
no concrete protocol exists yet (#548 adds the first)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest
from starlette.testclient import TestClient

from agentdeck.bindings import PROTOCOL_SPI_VERSION, BindingInfo, HttpEndpoint, StdioEndpoint
from agentdeck.deck import Deck
from agentdeck.errors import ConfigError


@pytest.fixture
def no_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


@dataclass
class _Recorder:
    started: list[str] = field(default_factory=list)
    stopped: list[str] = field(default_factory=list)


class _Http:
    """A binding that mounts a tiny ASGI app announcing its own name."""

    def __init__(
        self,
        name: str,
        path: str,
        recorder: _Recorder,
        *,
        on_start=None,
        advertises: frozenset[str] = frozenset(),
        projects: frozenset[str] | None = None,
        requires: frozenset[str] = frozenset(),
        spi_version: int = PROTOCOL_SPI_VERSION,
    ) -> None:
        self.info = BindingInfo(
            name=name,
            kind="protocol",
            transport="http",
            spi_version=spi_version,
            advertises=advertises,
            projects=advertises if projects is None else projects,
            requires=requires,
        )
        self._path = path
        self._recorder = recorder
        self._on_start = on_start

    def build(self, gateway: object) -> HttpEndpoint:
        name = self.info.name

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": name.encode()})

        return HttpEndpoint(path=self._path, app=app)

    async def start(self) -> None:
        if self._on_start is not None:
            self._on_start()
        self._recorder.started.append(self.info.name)

    async def stop(self) -> None:
        self._recorder.stopped.append(self.info.name)


class _Stdio:
    """A binding whose endpoint records that it ran, then returns (or waits forever)."""

    def __init__(self, name: str, recorder: _Recorder, *, forever: bool = False) -> None:
        self.info = BindingInfo(
            name=name, kind="surface", transport="stdio", spi_version=PROTOCOL_SPI_VERSION, advertises=frozenset()
        )
        self._recorder = recorder
        self._forever = forever

    def build(self, gateway: object) -> StdioEndpoint:
        async def run() -> None:
            self._recorder.started.append(f"{self.info.name}:run")
            if self._forever:
                await asyncio.Event().wait()

        return StdioEndpoint(run=run)

    async def start(self) -> None:
        self._recorder.started.append(self.info.name)

    async def stop(self) -> None:
        self._recorder.stopped.append(self.info.name)


# --- validation, pure -------------------------------------------------------------------------


def test_duplicate_http_paths_name_both_bindings_and_open_nothing(no_project):
    deck = Deck(agents=[])
    recorder = _Recorder()
    with pytest.raises(ConfigError, match="'a'.*'b'|'b'.*'a'"):
        deck.expose(_Http("a", "/x", recorder), _Http("b", "/x", recorder))
    assert not deck.is_open


def test_more_than_one_stdio_binding_names_both(no_project):
    deck = Deck(agents=[])
    recorder = _Recorder()
    with pytest.raises(ConfigError, match="'a'.*'b'|'b'.*'a'"):
        deck.expose(_Stdio("a", recorder), _Stdio("b", recorder))


def test_unsupported_spi_version_names_both_versions(no_project):
    deck = Deck(agents=[])
    recorder = _Recorder()
    with pytest.raises(ConfigError, match=r"spi_version=99.*spi_version=1"):
        deck.expose(_Http("a", "/a", recorder, spi_version=99))


def test_advertised_capability_with_no_projection_names_binding_and_capability(no_project):
    deck = Deck(agents=[])
    recorder = _Recorder()
    with pytest.raises(ConfigError, match="'a'.*streaming"):
        deck.expose(_Http("a", "/a", recorder, advertises=frozenset({"streaming"}), projects=frozenset()))


def test_missing_prerequisite_binding_is_named(no_project):
    deck = Deck(agents=[])
    recorder = _Recorder()
    with pytest.raises(ConfigError, match="'a'.*'ghost'"):
        deck.expose(_Http("a", "/a", recorder, requires=frozenset({"ghost"})))


def test_expose_opens_no_port_and_reads_no_stdin(no_project):
    """Validation alone must not touch the Deck."""
    deck = Deck(agents=[])
    recorder = _Recorder()
    deck.expose(_Http("a", "/a", recorder), _Stdio("b", recorder))
    assert not deck.is_open


# --- hosting -----------------------------------------------------------------------------------


def test_two_http_bindings_share_one_listener_each_sees_only_its_own_routes(no_project):
    deck = Deck(agents=[])
    recorder = _Recorder()
    exposure = deck.expose(_Http("a", "/a", recorder), _Http("b", "/b", recorder))

    with TestClient(exposure.asgi()) as client:
        assert client.get("/a").content == b"a"
        assert client.get("/b").content == b"b"
        assert client.get("/nope").status_code == 404


def test_stdio_and_http_binding_run_in_one_exposure(no_project):
    deck = Deck(agents=[])
    recorder = _Recorder()
    exposure = deck.expose(_Http("a", "/a", recorder), _Stdio("term", recorder, forever=True))

    with TestClient(exposure.asgi()) as client:
        assert client.get("/a").content == b"a"
        assert recorder.started == ["a", "term", "term:run"]

    assert recorder.stopped == ["term", "a"]


def test_failed_start_on_binding_three_rolls_back_and_closes_owned_deck(no_project):
    deck = Deck(agents=[])
    recorder = _Recorder()

    def boom():
        raise RuntimeError("boom")

    exposure = deck.expose(
        _Http("a", "/a", recorder),
        _Http("b", "/b", recorder),
        _Http("c", "/c", recorder, on_start=boom),
    )

    with pytest.raises(RuntimeError, match="boom"), TestClient(exposure.asgi()):
        pass

    assert recorder.started == ["a", "b"]
    assert recorder.stopped == ["b", "a"]
    assert not deck.is_open


@pytest.mark.asyncio
async def test_mounting_onto_an_already_open_deck_never_closes_it(no_project):
    deck = Deck(agents=[])
    recorder = _Recorder()
    await deck.__aenter__()
    try:
        exposure = deck.expose(_Http("a", "/a", recorder))
        with TestClient(exposure.asgi()) as client:
            assert client.get("/a").content == b"a"
        assert deck.is_open
    finally:
        await deck.aclose()


@pytest.mark.asyncio
async def test_serve_stdio_only_never_imports_uvicorn(no_project, monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "uvicorn", None)
    deck = Deck(agents=[])
    recorder = _Recorder()
    exposure = deck.expose(_Stdio("term", recorder))

    await exposure.serve()

    assert recorder.started == ["term", "term:run"]
    assert recorder.stopped == ["term"]
    assert not deck.is_open
