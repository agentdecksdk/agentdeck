"""Reading a user callable's signature: is a ``Context`` declared, which ``T``, what is left for
the model, and could any of it be established at all.

This module uses postponed annotations, so every subject defined here carries source strings
rather than objects  -  the case that breaks naive ``__annotations__`` reading, and the reason the
eager counterparts live in ``tests/context_injection_subjects.py``.

The failure this suite exists to prevent is a confident wrong answer. A decorator that replaced
a signature and one that preserved it look alike from outside; guessing "no context here" for
the first would drop an argument the callable needs, silently.
"""

from __future__ import annotations

import functools
import inspect
from typing import Any

import context_injection_subjects
import pytest

from agentdeck.authoring.injection import analyze_callable
from agentdeck.core.context import Context  # noqa: TC001  -  the subjects below must resolve it at runtime
from agentdeck.errors import ConfigError


class Calendar:
    """An application object, the sort a run is handed rather than told about."""


class Ledger:
    """A second one, so "the declared T" cannot pass by being the only type in the file."""


def no_context(date: str, attendees: int) -> None: ...


def one_context(date: str, environment: Context[Calendar]) -> None: ...


def two_contexts(here: Context[Calendar], also_here: Context[Ledger]) -> None: ...


def bare_context(date: str, ctx: Context) -> None: ...  # no type argument, on purpose


def unresolvable(value: NeverDefined) -> None: ...  # noqa: F821  -  the point is that it never resolves


def preserving(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return fn(*args, **kwargs)

    return wrapper


def destroying(fn):
    def wrapper(*args, **kwargs):
        return fn(*args, **kwargs)

    return wrapper


@preserving
def wrapped(date: str, environment: Context[Calendar]) -> None: ...


@destroying
def obscured(date: str, environment: Context[Calendar]) -> None: ...


class Bookings:
    def reserve(self, date: str, environment: Context[Calendar]) -> None: ...


def _names(analysis) -> list[str]:
    return [parameter.name for parameter in analysis.visible_parameters]


# --- how many Context parameters -------------------------------------------------------------


def test_a_callable_declaring_no_context_is_an_ordinary_callable() -> None:
    analysis = analyze_callable(no_context)

    assert analysis.reliable
    assert analysis.context_parameter is None
    assert analysis.context_type is None
    assert _names(analysis) == ["date", "attendees"]


def test_the_one_context_parameter_is_found_by_annotation_whatever_it_is_named() -> None:
    """Not ``ctx``, not first, not keyword-only  -  the annotation is the whole rule."""
    analysis = analyze_callable(one_context)

    assert analysis.context_parameter == "environment"
    assert analysis.context_type is Calendar


def test_the_context_parameter_is_withheld_from_the_model_visible_parameters() -> None:
    """What a tool schema gets built from must not contain an AgentDeck internal."""
    assert _names(analyze_callable(one_context)) == ["date"]


def test_two_context_parameters_are_a_configuration_error_naming_the_callable() -> None:
    with pytest.raises(ConfigError) as raised:
        analyze_callable(two_contexts)

    message = str(raised.value)
    assert "two_contexts" in message
    assert "here" in message and "also_here" in message
    assert "at most one" in message


def test_a_context_without_a_type_argument_still_injects() -> None:
    """Reading a bare ``Context`` as an ordinary parameter would hand it to the schema builder,
    which is exactly the leak the context rule forbids."""
    analysis = analyze_callable(bare_context)

    assert analysis.context_parameter == "ctx"
    assert analysis.context_type is Any
    assert _names(analysis) == ["date"]


# --- annotations that are not objects yet ------------------------------------------------------


def test_postponed_and_eager_annotations_analyze_identically() -> None:
    postponed = analyze_callable(one_context)
    eager = analyze_callable(context_injection_subjects.find_slots)

    assert (postponed.context_parameter, _names(postponed)) == (eager.context_parameter, _names(eager))
    assert postponed.context_type is Calendar
    assert eager.context_type is context_injection_subjects.Calendar


def test_visible_parameters_carry_resolved_annotations_not_source_strings() -> None:
    """A bridge rebuilds a signature from these. A string resolved against the bridge's module
    instead of the author's is a different type that happens to share a name."""
    (date,) = analyze_callable(one_context).visible_parameters

    assert date.annotation is str


def test_a_callable_whose_annotation_never_resolves_is_reported_unreliable() -> None:
    analysis = analyze_callable(unresolvable)

    assert not analysis.reliable
    assert analysis.context_parameter is None


# --- wrapped callables --------------------------------------------------------------------------


def test_a_wraps_decorated_callable_is_analyzed_as_the_function_it_wraps() -> None:
    analysis = analyze_callable(wrapped)

    assert analysis.reliable
    assert analysis.context_parameter == "environment"
    assert analysis.context_type is Calendar
    assert _names(analysis) == ["date"]


def test_a_decorator_that_replaced_the_signature_is_reported_unreliable_not_context_free() -> None:
    """``obscured`` does declare a context; nothing recoverable says so. Reporting "no context"
    here would read as a finding and drop the argument at the first call."""
    analysis = analyze_callable(obscured)

    assert not analysis.reliable
    assert analysis.context_parameter is None
    assert analysis.visible_parameters == ()


def test_a_zero_argument_callable_is_reliable_rather_than_mistaken_for_an_opaque_one() -> None:
    """The empty signature and the destroyed one both have no named parameters; only the second
    is unknowable."""
    analysis = analyze_callable(lambda: None)

    assert analysis.reliable
    assert analysis.visible_parameters == ()


# --- methods --------------------------------------------------------------------------------


def test_a_bound_method_hides_self_and_still_declares_its_context() -> None:
    analysis = analyze_callable(Bookings().reserve)

    assert analysis.context_parameter == "environment"
    assert _names(analysis) == ["date"]


def test_an_unbound_function_off_the_class_keeps_self_as_a_visible_parameter() -> None:
    """Nothing has bound an instance yet, so ``self`` is genuinely still an argument  -  the
    analysis reports the signature it was given rather than inferring a receiver."""
    analysis = analyze_callable(Bookings.reserve)

    assert analysis.context_parameter == "environment"
    assert _names(analysis) == ["self", "date"]


# --- the analysis reads, it does not build ------------------------------------------------------


def test_analysis_keeps_the_original_callable_rather_than_a_replacement() -> None:
    """Every later error message and the invocation-time safety net name the user's function."""
    assert analyze_callable(one_context).target is one_context


def test_analysis_preserves_parameter_kinds() -> None:
    def keyword_only(date: str, *, environment: Context[Calendar], attendees: int = 1) -> None: ...

    analysis = analyze_callable(keyword_only)

    kinds = {parameter.name: parameter.kind for parameter in analysis.visible_parameters}
    assert kinds == {
        "date": inspect.Parameter.POSITIONAL_OR_KEYWORD,
        "attendees": inspect.Parameter.KEYWORD_ONLY,
    }
    assert analysis.context_parameter == "environment"
