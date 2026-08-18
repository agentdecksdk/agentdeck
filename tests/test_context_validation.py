"""``Deck(context=T)`` and the build-time compatibility check it turns on.

Two failure shapes these tests exist to keep apart, because they look alike from outside and
only one of them is a finding:

* **incompatible**  -  the runtime can say the declared type does not meet the requirement, so
  ``build()`` refuses with a ``ContextTypeError`` naming both. This is the whole point of the
  declaration.
* **undecidable**  -  no runtime answer exists (a structural ``Protocol`` ``issubclass`` will not
  rule on, a ``TypeVar``, an engine-native tool object nothing introspects). ``build()`` accepts
  and the requirement stands or falls at invocation. Refusing here would reject builds that are
  very likely correct, and ``build()`` is not a partial type checker.

A deck declaring no ``context=`` is the third case and the one with the most existing behavior
riding on it: nothing is checked, exactly as before the declaration existed.

No live model: every assertion here is about ``build()``, which never calls one.
"""

from __future__ import annotations

from collections.abc import Mapping  # noqa: TC003  -  the subjects below must resolve it at runtime
from typing import Any, Protocol, TypeVar, runtime_checkable

import pytest
from agents import AgentHooks, WebSearchTool
from langgraph.graph import END, StateGraph
from pydantic import BaseModel

import agentdeck
from agentdeck.authoring import Agent, Workflow
from agentdeck.core.context import Context  # noqa: TC001  -  the subjects below must resolve it at runtime
from agentdeck.deck import Deck
from agentdeck.errors import ConfigError, ContextTypeError


class BaseContext:
    """The application environment a deck might declare."""


class MiddleContext(BaseContext):
    """A subtype of it  -  what a deck declares when its callables want the wider one."""


class GitHubContext:
    """An unrelated one, so "compatible" cannot pass by there being only one type in the file."""


@runtime_checkable
class Findable(Protocol):
    """A method-only protocol: ``issubclass`` *will* rule on this one."""

    def find(self) -> str: ...


@runtime_checkable
class HasSlot(Protocol):
    """A protocol with a data member: ``issubclass`` refuses to rule on it at all."""

    slot: str


class Structural(Protocol):
    """Not ``runtime_checkable``: ``issubclass`` raises rather than answering."""

    def find(self) -> str: ...


class Calendar:
    """Satisfies :class:`Findable` structurally, and nothing else here."""

    def find(self) -> str:
        return "09:00"


class Environment(dict[str, Any]):
    """A real ``Mapping`` subclass, for the runtime-ABC check."""


T = TypeVar("T")
"""Module scope on purpose: a ``TypeVar`` local to a test body never resolves, and the callable
would be reported unanalyzable long before the compatibility check saw it."""


class _State(BaseModel):
    request: str = ""
    out: str = ""


@pytest.fixture
def no_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


def _agent_with_tool(tool: Any) -> Agent:
    return Agent(name="Booker", instructions="Book things.", tools=[tool])


def _workflow_with_node(node: Any, *, name: str = "Book") -> Workflow:
    def build() -> StateGraph:
        graph = StateGraph(_State)
        graph.add_node("book", node)
        graph.set_entry_point("book")
        graph.add_edge("book", END)
        return graph

    return Workflow(name=name, state=_State, graph=build)


def _build(**kwargs: Any) -> Deck:
    return Deck(**kwargs).build()


# --- what the declared type satisfies ------------------------------------------------------------


def test_the_exact_declared_type_satisfies_a_requirement_for_it(no_project) -> None:
    async def find_slots(day: str, environment: Context[MiddleContext]) -> str:
        """Find free slots."""
        return day

    _build(agents=[_agent_with_tool(find_slots)], context=MiddleContext)


def test_a_subtype_satisfies_a_requirement_for_its_supertype(no_project) -> None:
    """The deck provides the narrower thing; the callable asked for the wider one and gets it."""

    async def find_slots(day: str, environment: Context[BaseContext]) -> str:
        """Find free slots."""
        return day

    _build(agents=[_agent_with_tool(find_slots)], context=MiddleContext)


def test_a_supertype_does_not_satisfy_a_requirement_for_a_subtype(no_project) -> None:
    """The other direction is exactly the mistake worth catching: the callable will reach for
    something the declared type does not have."""

    async def find_slots(day: str, environment: Context[MiddleContext]) -> str:
        """Find free slots."""
        return day

    with pytest.raises(ContextTypeError) as raised:
        _build(agents=[_agent_with_tool(find_slots)], context=BaseContext)

    message = str(raised.value)
    assert "find_slots" in message
    assert "MiddleContext" in message and "BaseContext" in message


def test_an_unrelated_declared_type_is_refused_naming_both_types(no_project) -> None:
    async def find_slots(day: str, environment: Context[MiddleContext]) -> str:
        """Find free slots."""
        return day

    with pytest.raises(ContextTypeError) as raised:
        _build(agents=[_agent_with_tool(find_slots)], context=GitHubContext)

    assert "requires MiddleContext" in str(raised.value)
    assert "provides GitHubContext" in str(raised.value)


def test_context_any_is_satisfied_by_anything(no_project) -> None:
    async def anything(environment: Context[Any]) -> str:
        """Take whatever is going."""
        return "ok"

    _build(agents=[_agent_with_tool(anything)], context=GitHubContext)


def test_a_bare_context_annotation_is_satisfied_by_anything(no_project) -> None:
    """A bare ``Context`` reads as ``Context[Any]`` everywhere else; it must here too, or the
    same annotation would mean two things."""

    async def anything(environment: Context) -> str:
        """Take whatever is going."""
        return "ok"

    _build(agents=[_agent_with_tool(anything)], context=GitHubContext)


async def _peek_mapping(environment: Context[Mapping[str, Any]]) -> str:
    """Read the environment."""
    return "ok"


def test_a_runtime_abc_is_satisfied_by_a_subclass_of_its_origin(no_project) -> None:
    """``Mapping[str, Any]`` is not a class and ``issubclass`` rejects it outright  -  the origin
    is the part of the annotation the runtime can genuinely check."""
    _build(agents=[_agent_with_tool(_peek_mapping)], context=Environment)


def test_a_runtime_abc_refuses_a_declared_type_that_is_not_one(no_project) -> None:
    with pytest.raises(ContextTypeError):
        _build(agents=[_agent_with_tool(_peek_mapping)], context=GitHubContext)


def test_a_union_requirement_is_satisfied_by_any_arm(no_project) -> None:
    """The regression this exists for: both spellings of a union have an origin that *is* a
    class on some Python versions, so falling through to ``issubclass`` would compare the
    declared type against ``UnionType`` itself and refuse a perfectly compatible deck."""

    async def peek(environment: Context[MiddleContext | None]) -> str:
        """Read the environment."""
        return "ok"

    _build(agents=[_agent_with_tool(peek)], context=MiddleContext)


def test_a_union_requirement_no_arm_satisfies_is_still_refused(no_project) -> None:
    async def peek(environment: Context[MiddleContext | GitHubContext]) -> str:
        """Read the environment."""
        return "ok"

    with pytest.raises(ContextTypeError):
        _build(agents=[_agent_with_tool(peek)], context=Calendar)


async def _peek_findable(environment: Context[Findable]) -> str:
    """Read the environment."""
    return "ok"


def test_a_runtime_checkable_method_protocol_accepts_a_type_that_implements_it(no_project) -> None:
    """One of the few protocols the runtime has a real answer for, so it gets a real check."""
    _build(agents=[_agent_with_tool(_peek_findable)], context=Calendar)


def test_a_runtime_checkable_method_protocol_refuses_a_type_that_does_not(no_project) -> None:
    with pytest.raises(ContextTypeError):
        _build(agents=[_agent_with_tool(_peek_findable)], context=GitHubContext)


# --- what defers instead of guessing --------------------------------------------------------------


def test_a_protocol_that_is_not_runtime_checkable_defers_rather_than_refusing(no_project) -> None:
    """``issubclass`` raises for this one. Refusing on that would reject a build that a static
    checker would pass, which is the line ``build()`` does not cross."""

    async def peek(environment: Context[Structural]) -> str:
        """Read the environment."""
        return "ok"

    _build(agents=[_agent_with_tool(peek)], context=GitHubContext)


def test_a_protocol_with_data_members_defers_rather_than_refusing(no_project) -> None:
    """``runtime_checkable`` is not enough  -  ``issubclass`` still refuses to rule on a protocol
    with a non-method member, and a refusal here would be a guess."""

    async def peek(environment: Context[HasSlot]) -> str:
        """Read the environment."""
        return "ok"

    _build(agents=[_agent_with_tool(peek)], context=GitHubContext)


def test_a_type_variable_requirement_defers(no_project) -> None:
    async def peek(environment: Context[T]) -> str:
        """Read the environment."""
        return "ok"

    _build(agents=[_agent_with_tool(peek)], context=GitHubContext)


def test_an_engine_native_tool_object_is_not_introspected(no_project) -> None:
    """A pre-built SDK tool carries no portability guarantee and no requirement anybody can
    read, so a declared context type has nothing to check it against."""
    _build(agents=[_agent_with_tool(WebSearchTool())], context=GitHubContext)


def test_a_node_whose_signature_cannot_be_read_is_still_left_alone(no_project) -> None:
    """Unanalyzable stays unanalyzable: declaring a context type must not turn a node the
    analysis reports nothing about into a refusal."""

    def destroying(fn):
        def wrapper(*args, **kwargs):
            return fn(*args, **kwargs)

        return wrapper

    @destroying
    async def book(state: _State, environment: Context[MiddleContext]) -> dict[str, Any]:
        return {"out": "ok"}

    _build(workflows=[_workflow_with_node(book)], context=GitHubContext)


# --- every injection site, not just tools ----------------------------------------------------------


def test_a_workflow_node_requirement_is_checked_and_names_the_node(no_project) -> None:
    async def book(state: _State, environment: Context[MiddleContext]) -> dict[str, Any]:
        return {"out": "ok"}

    with pytest.raises(ContextTypeError) as raised:
        _build(workflows=[_workflow_with_node(book)], context=GitHubContext)

    assert "node 'book'" in str(raised.value)
    assert "requires MiddleContext" in str(raised.value)


def test_an_instructions_callable_requirement_is_checked(no_project) -> None:
    def instructions(environment: Context[MiddleContext]) -> str:
        return "Book things."

    with pytest.raises(ContextTypeError) as raised:
        _build(agents=[Agent(name="Booker", instructions=instructions)], context=GitHubContext)

    assert "requires MiddleContext" in str(raised.value)


def test_a_hook_requirement_is_checked_and_names_the_method(no_project) -> None:
    class Hooks(AgentHooks[Any]):
        async def on_start(self, environment: Context[MiddleContext], agent: Any) -> None: ...

    with pytest.raises(ContextTypeError) as raised:
        _build(agents=[Agent(name="Booker", instructions="Book.", hooks=Hooks())], context=GitHubContext)

    assert "Hooks.on_start" in str(raised.value)
    assert "requires MiddleContext" in str(raised.value)


# --- the error type survives every wrap --------------------------------------------------------


def test_the_refusal_arrives_as_a_context_type_error_not_its_supertype(no_project) -> None:
    """Four call sites re-raise a compilation failure with a name prepended (the agent, the
    node, the hook method, the bundle file). Each one is a chance to flatten the class the API
    promises into the ``ConfigError`` it inherits from."""

    async def find_slots(day: str, environment: Context[MiddleContext]) -> str:
        """Find free slots."""
        return day

    with pytest.raises(ContextTypeError):
        _build(agents=[_agent_with_tool(find_slots)], context=GitHubContext)

    assert issubclass(ContextTypeError, ConfigError)


def test_the_refusal_survives_the_bundle_wrap_of_a_discovered_project(tmp_path, monkeypatch) -> None:
    """``from_project`` re-raises a bundle's compile failure as "<file> failed to build"  -  the
    one wrap that catches a bare ``Exception``, and so the easiest place to lose the class."""
    project = tmp_path / ".agentdeck" / "agents" / "booker"
    project.mkdir(parents=True)
    (project / "agent.py").write_text(
        "from agentdeck import Agent, Context\n"
        "from test_context_validation import MiddleContext\n"
        "\n"
        "async def find_slots(day: str, environment: Context[MiddleContext]) -> str:\n"
        '    """Find free slots."""\n'
        "    return day\n"
        "\n"
        'booker = Agent(name="Booker", instructions="Book things.", tools=[find_slots])\n'
    )
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ContextTypeError) as raised:
        Deck.from_project(context=GitHubContext).build()

    assert "failed to build" in str(raised.value)
    assert "requires MiddleContext" in str(raised.value)


def test_context_type_error_is_exported_from_the_package_root() -> None:
    assert agentdeck.ContextTypeError is ContextTypeError
    assert "ContextTypeError" in agentdeck.__all__


# --- the declaration is a type, and a deck may still declare none ---------------------------------


def test_declaring_an_instance_instead_of_a_type_is_refused(no_project) -> None:
    """The natural mistake, and the one that would make the whole declaration useless: an
    instance satisfies no check, so every requirement would silently defer."""
    with pytest.raises(ConfigError) as raised:
        Deck(context=MiddleContext())

    assert "Deck(context=...)" in str(raised.value)
    assert "deck.run(" in str(raised.value)


def test_declaring_a_union_is_refused_rather_than_deferring_everywhere(no_project) -> None:
    with pytest.raises(ConfigError):
        Deck(context=MiddleContext | None)


def test_a_parameterised_generic_may_be_declared(no_project) -> None:
    async def peek(environment: Context[Mapping[str, Any]]) -> str:
        """Read the environment."""
        return "ok"

    _build(agents=[_agent_with_tool(peek)], context=dict[str, Any])


def test_a_deck_declaring_no_context_checks_nothing(no_project) -> None:
    """The behavior every deck built before this parameter existed relies on: a requirement is
    compiled and left to the run, not refused for want of a declaration."""

    async def find_slots(day: str, environment: Context[MiddleContext]) -> str:
        """Find free slots."""
        return day

    async def book(state: _State, environment: Context[GitHubContext]) -> dict[str, Any]:
        return {"out": "ok"}

    _build(agents=[_agent_with_tool(find_slots)], workflows=[_workflow_with_node(book)])
