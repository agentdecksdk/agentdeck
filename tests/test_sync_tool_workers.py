"""``docs/delivery/plan-sync-native-tools.md``'s verification table, item by item.

``SyncToolWorkers`` itself (bounded concurrency, cancel-before-execution, shutdown) is tested
directly against the pool: deterministic, and no Deck lifecycle needed to observe it. The
cancellation-through-a-run and mixed-run guarantees need a live Deck, since they are about what
``_play`` does with the pool's result, not the pool.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

from agentdeck import Deck, ToolCtx, WorkflowCtx, tool, workflow
from agentdeck.core.status import RunStatus
from agentdeck.core.workers import SyncToolWorkers
from agentdeck.errors import ConfigError


@pytest.fixture(autouse=True)
def _no_project(tmp_path, monkeypatch):
    """A cwd with no ``.agentdeck``: every catalog here is code-first."""
    monkeypatch.chdir(tmp_path)


async def _settles(run: Any, status: RunStatus) -> None:
    """Wait for the run to reach ``status``. The body runs in its own task, so a test that
    asserted immediately would be racing it."""
    for _ in range(500):
        if await run.status() is status:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"run {run.id} never reached {status}, last seen {await run.status()}")


# --- SyncToolWorkers on its own: bounded concurrency, cancel-before-start, shutdown ---------


async def test_worker_concurrency_is_bounded() -> None:
    """N+1 concurrent calls against an N-worker pool: the (N+1)th waits."""
    workers = SyncToolWorkers(max_workers=1)
    first_started = threading.Event()
    release_first = threading.Event()
    order: list[str] = []

    def first() -> None:
        first_started.set()
        release_first.wait(timeout=5)
        order.append("first")

    def second() -> None:
        order.append("second")

    async def run_second_once_first_is_running() -> None:
        await asyncio.to_thread(first_started.wait, 5)
        await workers.submit(second)

    async def release_once_second_is_queued() -> None:
        await asyncio.to_thread(first_started.wait, 5)
        await asyncio.sleep(0.05)  # let `second` reach the pool's queue behind `first`
        assert order == [], "second ran while the one worker was still busy with first"
        release_first.set()

    await asyncio.gather(workers.submit(first), run_second_once_first_is_running(), release_once_second_is_queued())

    assert order == ["first", "second"]
    await workers.aclose()


async def test_a_queued_job_cancelled_before_it_starts_never_calls_its_body() -> None:
    """A queued sync tool cancelled before its worker starts never has its body called."""
    workers = SyncToolWorkers(max_workers=1)
    release = threading.Event()
    called: list[str] = []

    def occupy() -> None:
        release.wait(timeout=5)

    def queued() -> None:
        called.append("ran")

    async def cancel_queued_before_it_starts() -> None:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(workers.submit(queued), timeout=0.05)

    async def release_the_worker_once_cancelled() -> None:
        await asyncio.sleep(0.2)
        release.set()

    await asyncio.gather(workers.submit(occupy), cancel_queued_before_it_starts(), release_the_worker_once_cancelled())

    assert called == []
    await workers.aclose()


async def test_aclose_itself_drops_a_queued_job_no_task_ever_cancelled() -> None:
    """The other route to a dropped queued job: nobody cancels the awaiting task this time  -
    ``aclose()``'s own ``shutdown(cancel_futures=True)`` drops it from the pool directly."""
    workers = SyncToolWorkers(max_workers=1)
    started = threading.Event()
    release = threading.Event()
    called: list[str] = []

    def occupy() -> None:
        started.set()
        release.wait(timeout=5)

    def queued() -> None:
        called.append("ran")

    async def submit_queued_and_expect_cancellation() -> None:
        await asyncio.to_thread(started.wait, 5)
        await asyncio.sleep(0.05)  # let `queued` reach the pool's queue behind `occupy`
        with pytest.raises(asyncio.CancelledError):
            await workers.submit(queued)

    async def close_while_occupy_is_still_running() -> None:
        await asyncio.to_thread(started.wait, 5)
        await asyncio.sleep(0.1)  # `queued` is queued and `occupy` is genuinely still running
        await workers.aclose()

    async def release_once_aclose_has_already_cancelled_it() -> None:
        await asyncio.to_thread(started.wait, 5)
        await asyncio.sleep(0.2)  # after aclose()'s cancel-drain, so it never races the cancel
        release.set()

    await asyncio.gather(
        workers.submit(occupy),
        submit_queued_and_expect_cancellation(),
        close_while_occupy_is_still_running(),
        release_once_aclose_has_already_cancelled_it(),
    )
    assert called == []


async def test_shutdown_drains_a_running_job_and_rejects_new_ones() -> None:
    """No jobs, a running job, and a submission after close, in one pass: each drains or rejects
    correctly, and closing never blocks the event loop the running job still needs (a worker
    blocked on the reporter bridge would deadlock against a synchronously-blocking shutdown)."""
    empty = SyncToolWorkers()
    await empty.aclose()  # nothing running: returns immediately

    workers = SyncToolWorkers(max_workers=1)
    started = threading.Event()
    finished = threading.Event()

    def slow() -> str:
        started.set()
        finished.wait(timeout=5)
        return "done"

    async def run_slow() -> str:
        return await workers.submit(slow)

    async def close_once_running() -> None:
        await asyncio.to_thread(started.wait, 5)
        # The loop must still turn while this awaits: nothing else releases `finished`.
        finished.set()
        await workers.aclose()

    results = await asyncio.gather(run_slow(), close_once_running())
    assert results[0] == "done"

    with pytest.raises(RuntimeError):
        await workers.submit(slow)


# --- through a real Deck: what `_play` does with the pool's result ------------------------


async def test_cancel_during_a_sync_tools_execution_never_completes_the_run() -> None:
    """A sync body offers no safepoint of its own; ``run.cancel()`` while it is still on its
    worker must still end the run as CANCELLED, never COMPLETED, once the body eventually
    returns  -  it cannot be interrupted, but its result must not overwrite the verdict.

    A bare ``@tool`` is no longer a run `Deck.run`/``Runs.start`` takes by name (#488), so it
    is reached the way any body reaches one: ``ctx.invoke`` from a thin wrapping ``@workflow``.
    Nothing else about the case changes  -  the assertions still read the tool's own run.
    """
    started = threading.Event()
    release = threading.Event()
    finished: list[str] = []
    held: dict[str, Any] = {}

    @tool
    def blocking() -> str:
        started.set()
        release.wait(timeout=5)
        finished.append("ran to the end regardless")
        return "too late to matter"

    @workflow
    async def running(ctx: WorkflowCtx) -> str:
        child = ctx.invoke(blocking)
        held["child"] = child
        try:
            return str(await child)
        except RuntimeError:
            return "cancelled"  # the cancel this test causes; discarded the same as the body's own result

    async with Deck(workflows=[blocking, running]) as deck:
        await deck.runs.start("running", None)
        await asyncio.to_thread(started.wait, 5)
        child = held["child"]
        await child.cancel("operator said stop")
        release.set()
        await _settles(child, RunStatus.CANCELLED)

        assert finished == ["ran to the end regardless"]
        kinds = [event.kind async for event in child.events()]
        assert kinds[-1] == "run.cancelled"
        assert "run.completed" not in kinds


async def test_a_sync_bodys_own_failure_is_discarded_once_cancelled() -> None:
    """The RunFailed half of plan §3: a sync body that raises after the run was already
    cancelled must not surface as ``run.failed``  -  the cancel takes precedence, and the raise
    the body could not be stopped from making is discarded the same as a return would be.

    Reached through a wrapping ``@workflow`` and ``ctx.invoke``, as above (#488): a bare
    ``@tool`` is no longer a name ``Runs.start`` takes.
    """
    started = threading.Event()
    release = threading.Event()
    held: dict[str, Any] = {}

    @tool
    def failing() -> str:
        started.set()
        release.wait(timeout=5)
        raise ValueError("too late to matter")

    @workflow
    async def running(ctx: WorkflowCtx) -> str:
        child = ctx.invoke(failing)
        held["child"] = child
        try:
            return str(await child)
        except RuntimeError:
            return "cancelled"

    async with Deck(workflows=[failing, running]) as deck:
        await deck.runs.start("running", None)
        await asyncio.to_thread(started.wait, 5)
        child = held["child"]
        await child.cancel("operator said stop")
        release.set()
        await _settles(child, RunStatus.CANCELLED)

        kinds = [event.kind async for event in child.events()]
        assert kinds[-1] == "run.cancelled"
        assert "run.failed" not in kinds


async def test_deck_aclose_drains_a_still_running_sync_tool_without_deadlocking() -> None:
    """Plan §4, the trap the deadlock check exists for: ``NativeExecutor.aclose()`` cancels the
    parked body's *task* before the pool ever sees ``aclose()``, which must not make the pool
    think the job itself is done  -  closing while genuinely still running must drain, not block
    the loop the worker's reporter bridge would otherwise need to unblock it.

    Reached through ``ctx.invoke`` (#488): a bare ``@tool`` is no longer a name ``Runs.start``
    takes.
    """
    started = threading.Event()
    release = threading.Event()
    finished: list[str] = []

    @tool
    def slow() -> str:
        started.set()
        release.wait(timeout=5)
        finished.append("done")
        return "done"

    @workflow
    async def running(ctx: WorkflowCtx) -> str:
        return str(await ctx.invoke(slow))

    deck = Deck(workflows=[slow, running])
    await deck.__aenter__()
    await deck.runs.start("running", None)
    await asyncio.to_thread(started.wait, 5)

    async def release_shortly_after_close_begins() -> None:
        await asyncio.sleep(0.1)
        release.set()

    # If aclose() ever falls through to a blocking shutdown(wait=True), this task never gets a
    # turn to release the worker and the outer wait_for is what catches the hang.
    await asyncio.wait_for(asyncio.gather(deck.aclose(), release_shortly_after_close_begins()), timeout=5)

    assert finished == ["done"]


async def test_a_blocking_sync_tool_does_not_delay_an_unrelated_concurrent_run() -> None:
    """The core regression #516 left open: a blocking sync tool on a worker must never stall an
    unrelated run sharing the same loop.

    ``slow`` is reached through a thin wrapping ``@workflow`` and ``ctx.invoke`` (#488): a bare
    ``@tool`` is no longer a name ``Deck.run`` takes.
    """
    order: list[str] = []
    holding = threading.Event()
    release = threading.Event()

    @tool
    def slow() -> str:
        holding.set()
        release.wait(timeout=5)
        order.append("slow")
        return "slow done"

    @workflow
    async def running(ctx: WorkflowCtx) -> str:
        return str(await ctx.invoke(slow))

    @workflow
    async def fast(ctx: WorkflowCtx) -> str:
        order.append("fast")
        return "fast done"

    async def run_slow() -> Any:
        return await deck.run("running", None)

    async def run_fast_once_slow_is_running() -> Any:
        await asyncio.to_thread(holding.wait, 5)
        result = await deck.run("fast", None)
        release.set()
        return result

    async with Deck(workflows=[slow, running, fast]) as deck:
        slow_result, fast_result = await asyncio.gather(run_slow(), run_fast_once_slow_is_running())

    assert (slow_result, fast_result) == ("slow done", "fast done")
    # `fast` finished and was recorded first: the event loop was never stalled behind `slow`.
    assert order == ["fast", "slow"]


async def test_ctx_data_and_reporter_work_from_a_sync_body_with_no_orchestration() -> None:
    """``ctx.data``/``ctx.reporter`` work from a sync body; ``ctx.invoke``/``parallel``/``ask``/
    ``approve`` are absent (leaf-only surface, unchanged by this plan); ``ctx.safepoint()`` is
    refused explicitly rather than silently doing nothing.

    Reached through ``ctx.invoke`` (#488): a bare ``@tool`` is no longer a name ``Deck.run``
    takes. ``context`` still reaches it, by reference, through the wrapping run.
    """
    seen: dict[str, Any] = {}

    @tool
    def inspecting(ctx: ToolCtx[str]) -> str:
        seen["data"] = ctx.data
        ctx.reporter.info("looked")
        for missing in ("invoke", "parallel", "ask", "approve"):
            seen[missing] = hasattr(ctx, missing)
        try:
            ctx.safepoint()
        except ConfigError as refused:
            seen["safepoint_error"] = str(refused)
        return "ok"

    @workflow
    async def running(ctx: WorkflowCtx[str]) -> str:
        return str(await ctx.invoke(inspecting))

    async with Deck(workflows=[inspecting, running]) as deck:
        assert await deck.run("running", None, context="the data") == "ok"

    assert seen["data"] == "the data"
    assert seen["invoke"] is False
    assert seen["parallel"] is False
    assert seen["ask"] is False
    assert seen["approve"] is False
    assert "sync @tool body" in seen["safepoint_error"]
