"""A tool that raises is recorded on ``tool.call.completed.error`` (#250).

The failure never surfaces as an exception the caller sees: the Agents SDK catches it deep inside
its own dispatch and hands it to ``failure_error_function`` to be turned into a string the model
reads. That string is the only thing that used to survive, so the run completed and nothing
machine-readable said a tool had failed.

Two halves, tested apart because they run at different times: ``compile_tool``'s failure formatter
writes the exception onto the run's context while the tool call is being handled, and the
translator reads it back when the paired result item arrives. No live model — the tools below are
driven through the SDK's own ``on_invoke_tool``, the same entry point its run loop uses.
"""

from __future__ import annotations

import types
from typing import Any

import pytest
from agents import default_tool_error_function
from agents.tool_context import ToolContext

from agentdeck.adapters.engines.openai_agents.translate import translate
from agentdeck.authoring.tools import compile_tool
from agentdeck.core.context import Context, RunContext
from agentdeck.core.events import RESULT_PREVIEW_MAX, ToolCallCompleted


class Calendar:
    """Stands in for whatever an application hands a run."""


async def explode(reason: str) -> str:
    """A tool that fails the way a real one does — a plain callable, no context declared."""
    raise RuntimeError(f"boom: {reason}")


async def explode_with_context(reason: str, environment: Context[Calendar]) -> str:
    """The same failure on the other compile branch, the one that goes through ``_bridge``."""
    raise RuntimeError(f"boom: {reason}")


async def succeed(reason: str) -> str:
    """A tool that does not fail."""
    return f"fine: {reason}"


def _invoke(tool: Any, run: RunContext, arguments: str = '{"reason": "test"}', call_id: str = "call_1") -> Any:
    """Call ``tool`` the way the SDK's run loop does — through its own dispatch, not around it."""
    context = ToolContext(context=run, tool_name=tool.name, tool_call_id=call_id, tool_arguments=arguments)
    return tool.on_invoke_tool(context, arguments)


def _result_item(call_id: str, output: str) -> Any:
    """The stream event the SDK emits once a tool call has produced its result."""
    item = types.SimpleNamespace(type="tool_call_output_item", call_id=call_id, output=output)
    return types.SimpleNamespace(type="run_item_stream_event", item=item)


# --- the failure is recorded, on both compile branches ------------------------------------------


@pytest.mark.parametrize("target", [explode, explode_with_context], ids=["plain", "with-context"])
async def test_a_raised_tool_records_its_exception_on_the_run_context(target: Any) -> None:
    """Both branches of ``compile_tool`` pass the formatter. The one that declares ``Context[T]``
    is compiled through ``_bridge``, which forwards nothing else, so it is the branch that would
    silently lose this if the kwarg were passed on only one side.
    """
    run = RunContext(run_id="r", data=Calendar())

    await _invoke(compile_tool(target, context_type=Calendar), run)

    assert run.tool_failures == {"call_1": "RuntimeError: boom: test"}


async def test_a_tool_that_succeeds_records_nothing() -> None:
    run = RunContext(run_id="r")

    assert await _invoke(compile_tool(succeed), run) == "fine: test"
    assert run.tool_failures == {}


async def test_two_failing_calls_are_kept_apart_by_call_id() -> None:
    """Tool calls in one turn can be dispatched in parallel, so the record cannot be a single slot."""
    run = RunContext(run_id="r")
    tool = compile_tool(explode)

    await _invoke(tool, run, '{"reason": "first"}', call_id="call_1")
    await _invoke(tool, run, '{"reason": "second"}', call_id="call_2")

    assert run.tool_failures == {
        "call_1": "RuntimeError: boom: first",
        "call_2": "RuntimeError: boom: second",
    }


# --- what the model sees does not move ----------------------------------------------------------


async def test_the_model_still_reads_the_sdk_s_own_default_message() -> None:
    """The whole point of delegating rather than writing our own string: this issue is about
    observability, and a different message here would change how the agent behaves while
    claiming not to.
    """
    run = RunContext(run_id="r")
    error = RuntimeError("boom: test")
    expected = default_tool_error_function(
        ToolContext(context=run, tool_name="explode", tool_call_id="call_1", tool_arguments="{}"), error
    )

    assert await _invoke(compile_tool(explode), run) == expected


async def test_the_json_decode_message_keeps_its_own_wording() -> None:
    """``default_tool_error_function`` special-cases a malformed-arguments failure into different
    prose. Delegating is what keeps that branch; a hand-written ``f"...{error}"`` would flatten it.
    """
    run = RunContext(run_id="r")

    message = await _invoke(compile_tool(succeed), run, "not json at all")

    assert message.startswith("An error occurred while parsing tool arguments.")
    assert run.tool_failures != {}


async def test_a_base_exception_is_not_swallowed() -> None:
    """The SDK catches ``Exception``. A cancellation or a Ctrl-C has to keep travelling, or a
    shutdown would be reported to the model as a tool result and retried.
    """

    async def interrupted(reason: str) -> str:
        """Raises past the SDK's handler."""
        raise KeyboardInterrupt

    run = RunContext(run_id="r")

    with pytest.raises(KeyboardInterrupt):
        await _invoke(compile_tool(interrupted), run)

    assert run.tool_failures == {}


# --- the translator reads it back onto the event ------------------------------------------------


def test_the_recorded_failure_lands_on_the_paired_event() -> None:
    failures = {"call_1": "RuntimeError: boom: test"}

    payload = translate(_result_item("call_1", "error prose the model reads"), {"call_1": "explode"}, failures)

    assert isinstance(payload, ToolCallCompleted)
    assert payload.error == "RuntimeError: boom: test"
    assert payload.result_preview == "error prose the model reads"


def test_a_call_that_did_not_fail_leaves_error_none() -> None:
    payload = translate(_result_item("call_1", "fine: test"), {"call_1": "succeed"}, {})

    assert isinstance(payload, ToolCallCompleted)
    assert payload.error is None


def test_the_record_is_consumed_so_it_cannot_be_reused() -> None:
    """One record belongs to one call. Leaving it behind would attach the first call's exception
    to a later call that reused the id, and grow the dict for the run's whole lifetime.
    """
    failures = {"call_1": "RuntimeError: boom: test"}

    translate(_result_item("call_1", "prose"), {}, failures)

    assert failures == {}


def test_the_error_is_capped_the_same_way_result_preview_is() -> None:
    """A traceback carries whatever the failing call's arguments carried, so it gets the same
    ceiling as the result it sits beside.
    """
    failures = {"call_1": "RuntimeError: " + "x" * (RESULT_PREVIEW_MAX * 2)}

    payload = translate(_result_item("call_1", "prose"), {}, failures)

    assert isinstance(payload, ToolCallCompleted)
    assert payload.error is not None
    assert len(payload.error) == RESULT_PREVIEW_MAX
