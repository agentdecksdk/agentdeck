"""The two injection sites that are neither a tool nor a node: dynamic instructions, and an
agent's lifecycle hooks.

Both go through the same analysis a tool does, which is the point — a second mechanism here
would be a second set of rules about what ``Context[...]`` means, and the two would drift.

The property these tests are shaped around is the design's strongest rule, at the one site
where breaking it would be invisible: **only what an instructions callable returns reaches the
prompt.** ``ctx.data`` is never projected into it, so the central assertion is again an absence
— the environment's secret must not appear in the system prompt the model was handed.

No live model: the SDK boundary is a scripted model that records the instructions it was given.
"""

from __future__ import annotations

import functools
from typing import Any

import pytest
from agents.lifecycle import AgentHooks
from scripted_model import ScriptedModel, patch_provider, provider_of

from agentdeck.authoring import Agent
from agentdeck.authoring.hooks import compile_hooks
from agentdeck.authoring.instructions import compile_instructions
from agentdeck.core.context import Context, RunContext  # noqa: TC001 — the subjects resolve it at runtime
from agentdeck.deck import Deck
from agentdeck.errors import ConfigError


class Business:
    """The application object a run is handed."""

    def __init__(self, name: str = "Acme Dental", secret: str = "the-private-note") -> None:
        self.name = name
        self.secret = secret


class RecordingModel(ScriptedModel):
    """A scripted model that also keeps the system prompt it was handed — the only place from
    which "what the model actually saw" can be asserted."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.instructions: list[Any] = []

    async def stream_response(self, _instructions: Any = None, input: Any = None, *a: Any, **k: Any):
        self.instructions.append(_instructions)
        async for event in super().stream_response(_instructions, input, *a, **k):
            yield event


class _Wrapper:
    """Stands in for the SDK's ``RunContextWrapper`` where a unit test calls a compiled
    instructions callable directly."""

    def __init__(self, context: Any) -> None:
        self.context = context


@pytest.fixture
def no_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


# --- dynamic instructions ---------------------------------------------------------------------


async def test_an_instructions_callable_receives_the_context_and_returns_the_prompt() -> None:
    def instructions(environment: Context[Business]) -> str:
        return f"Business: {environment.data.name}"

    compiled = compile_instructions(instructions)

    assert await compiled(_Wrapper(RunContext(run_id="r", data=Business())), None) == "Business: Acme Dental"


async def test_a_synchronous_and_an_async_instructions_callable_behave_the_same() -> None:
    async def eventually(environment: Context[Business]) -> str:
        return environment.data.name

    def immediately(environment: Context[Business]) -> str:
        return environment.data.name

    wrapper = _Wrapper(RunContext(run_id="r", data=Business()))

    assert await compile_instructions(eventually)(wrapper, None) == "Acme Dental"
    assert await compile_instructions(immediately)(wrapper, None) == "Acme Dental"


async def test_an_instructions_callable_that_declares_no_context_is_still_compiled() -> None:
    """Zero ``Context[...]`` parameters is an ordinary callable everywhere else too."""

    def instructions() -> str:
        return "static, but computed"

    compiled = compile_instructions(instructions)

    assert await compiled(_Wrapper("not a run context at all"), None) == "static, but computed"


async def test_a_wraps_decorated_instructions_callable_is_analyzed_as_what_it_wraps() -> None:
    def logged(fn: Any) -> Any:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return fn(*args, **kwargs)

        return wrapper

    @logged
    async def instructions(environment: Context[Business]) -> str:
        return environment.data.name

    compiled = compile_instructions(instructions)

    assert await compiled(_Wrapper(RunContext(run_id="r", data=Business())), None) == "Acme Dental"


def test_an_instructions_callable_with_a_parameter_nothing_supplies_is_refused() -> None:
    """Unlike a tool, an instructions callable has no model-supplied arguments to leave room
    for — so a leftover parameter has no reading under which it would ever be filled."""

    def instructions(tone: str, environment: Context[Business]) -> str:
        return tone

    with pytest.raises(ConfigError) as raised:
        compile_instructions(instructions)

    assert "tone" in str(raised.value)
    assert "at most one Context" in str(raised.value)


def test_instructions_whose_signature_cannot_be_read_are_refused() -> None:
    def destroying(fn: Any) -> Any:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return fn(*args, **kwargs)

        return wrapper

    @destroying
    def instructions(environment: Context[Business]) -> str:
        return "never"

    with pytest.raises(ConfigError, match="signature could not be read"):
        compile_instructions(instructions)


def test_two_context_parameters_in_instructions_are_a_configuration_error() -> None:
    def instructions(here: Context[Business], also: Context[Business]) -> str:
        return "never"

    with pytest.raises(ConfigError, match="at most one"):
        compile_instructions(instructions)


async def test_instructions_played_by_a_foreign_run_say_so() -> None:
    def instructions(environment: Context[Business]) -> str:
        return "never"

    with pytest.raises(ConfigError, match="AgentDeck run context"):
        await compile_instructions(instructions)(_Wrapper("some other framework's context"), None)


# --- end to end: what reaches the model ---------------------------------------------------------


@pytest.mark.asyncio
async def test_only_the_return_value_of_an_instructions_callable_reaches_the_prompt(no_project, monkeypatch) -> None:
    """The rule that must not be weakened, at the site that could weaken it silently. The
    callable holds the whole environment and names one field of it; nothing else may travel."""
    seen: list[Any] = []

    def instructions(environment: Context[Business]) -> str:
        seen.append(environment.data)
        return f"You work for {environment.data.name}."

    model = RecordingModel(deltas=("ok",))
    patch_provider(monkeypatch, provider_of(model))
    business = Business()
    deck = Deck(agents=[Agent(name="Front", instructions=instructions)])
    deck.build()

    async with deck:
        await deck.run("Front", "hello", context=business)

    assert seen[0] is business
    assert model.instructions == ["You work for Acme Dental."]
    assert business.secret not in str(model.instructions)


@pytest.mark.asyncio
async def test_a_plain_string_of_instructions_is_unchanged(no_project, monkeypatch) -> None:
    """Every agent written before this existed compiles to exactly the same prompt."""
    model = RecordingModel(deltas=("ok",))
    patch_provider(monkeypatch, provider_of(model))
    deck = Deck(agents=[Agent(name="Front", instructions="Be brief.")])
    deck.build()

    async with deck:
        await deck.run("Front", "hello")

    assert model.instructions == ["Be brief."]


def test_an_instructions_callable_that_cannot_be_compiled_fails_at_build(no_project) -> None:
    def instructions(tone: str) -> str:
        return tone

    deck = Deck(agents=[Agent(name="Front", instructions=instructions)])

    with pytest.raises(ConfigError, match="tone"):
        deck.build()


# --- hooks ----------------------------------------------------------------------------------------


class _PlainHooks(AgentHooks[Any]):
    """Engine-native: names the SDK's own wrapper, declares no ``Context``."""

    def __init__(self) -> None:
        self.started: list[Any] = []

    async def on_start(self, context: Any, agent: Any) -> None:
        self.started.append(context)


class _ContextHooks(AgentHooks[Any]):
    """Portable: names AgentDeck's type where the SDK's wrapper would go."""

    def __init__(self) -> None:
        self.started: list[Any] = []
        self.ended: list[Any] = []

    async def on_start(self, environment: Context[Business], agent: Any) -> None:
        self.started.append((environment, agent))

    async def on_end(self, context: Any, agent: Any, output: Any) -> None:
        # Deliberately not bridged: a partially portable hooks object must keep working.
        self.ended.append(output)


def test_hooks_declaring_no_context_are_passed_straight_through() -> None:
    """Engine-native, and nothing here introspects or wraps it."""
    hooks = _PlainHooks()

    assert compile_hooks(hooks) is hooks
    assert compile_hooks(None) is None


def test_a_hook_declaring_its_context_anywhere_but_first_is_refused() -> None:
    class Misplaced(AgentHooks[Any]):
        async def on_start(self, agent: Any, environment: Context[Business]) -> None: ...

    with pytest.raises(ConfigError) as raised:
        compile_hooks(Misplaced())

    message = str(raised.value)
    assert "Misplaced.on_start" in message
    assert "receives it first" in message


@pytest.mark.asyncio
async def test_a_hook_declaring_a_context_receives_it_during_a_real_run(no_project, monkeypatch) -> None:
    patch_provider(monkeypatch, provider_of(ScriptedModel(deltas=("ok",))))
    hooks = _ContextHooks()
    business = Business()
    deck = Deck(agents=[Agent(name="Front", instructions="be brief", hooks=hooks)])
    deck.build()

    async with deck:
        await deck.run("Front", "hello", context=business)

    (environment, agent), *rest = hooks.started
    assert rest == []
    assert isinstance(environment, Context)
    assert environment.data is business
    assert agent.name == "Front"


@pytest.mark.asyncio
async def test_an_unbridged_hook_on_the_same_object_still_reaches_the_original(no_project, monkeypatch) -> None:
    """Only the declaring methods are rewritten; the rest are forwarded untouched, so a hooks
    class that is half portable does not lose the half that is not."""
    patch_provider(monkeypatch, provider_of(ScriptedModel(deltas=("ok",))))
    hooks = _ContextHooks()
    deck = Deck(agents=[Agent(name="Front", instructions="be brief", hooks=hooks)])
    deck.build()

    async with deck:
        await deck.run("Front", "hello", context=Business())

    assert hooks.ended  # on_end was never bridged, and still ran
