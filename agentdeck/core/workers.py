"""One bounded worker pool for every sync ``@tool`` body a deck runs.

Both the openai-agents-facing bridge (``authoring/tools.py``) and the native executor
(``adapters/executors/native/executor.py``) need to hand a sync body to a thread they own rather
than the interpreter-global default executor ``asyncio.to_thread()`` reaches for. Neither ring may
import the other, so this lives in ``core/``, stdlib only, next to :mod:`~agentdeck.core.control`
and :mod:`~agentdeck.core.reporting`  -  the other two primitives both call sites already share.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import Future, ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


class SyncToolWorkers:
    """One shared :class:`~concurrent.futures.ThreadPoolExecutor`, and nothing wider: submit a
    callable, await its result, drain and shut down cleanly. It knows nothing of ``Run``
    lifecycle or event persistence  -  that stays the executor's.

    ``max_workers=None`` keeps Python's own default (``min(32, cpu_count + 4)``); nothing here
    adds a settings knob for it.
    """

    def __init__(self, max_workers: int | None = None) -> None:
        self._pool = ThreadPoolExecutor(max_workers=max_workers)
        # Tracked so `aclose` can drain exactly the futures still running, without a second
        # bookkeeping structure for the pool to disagree with.
        self._pending: set[Future[Any]] = set()

    async def submit[T](self, func: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
        """Run ``func(*args, **kwargs)`` on the pool and await its result.

        ``asyncio.wrap_future`` already gives this the cancellation behavior a queued-not-started
        job needs for free: cancelling the awaiting task cancels the underlying future too, so a
        job that never started never runs its body. Raises ``RuntimeError`` if called after
        :meth:`aclose`, the same as the executor's own ``submit`` does past ``shutdown()``.

        ``_pending`` is untracked only once the *future* is done, not once this call stops
        awaiting it: a caller cancelled out from under a still-running job (the native executor
        cancels a parked body's task on its own ``aclose()``) must not make :meth:`aclose` think
        there is nothing left to drain, or the pool's own close would fall through to a blocking
        ``shutdown(wait=True)`` while the job is still on its thread.
        """
        loop = asyncio.get_running_loop()
        future = self._pool.submit(func, *args, **kwargs)
        self._pending.add(future)
        future.add_done_callback(lambda done: loop.call_soon_threadsafe(self._pending.discard, done))
        return await asyncio.wrap_future(future, loop=loop)

    async def aclose(self) -> None:
        """Stop accepting submissions, drop queued-not-started work, and let running work finish.

        ``shutdown(wait=True)`` run straight away would block the event loop for as long as any
        worker keeps running  -  and a worker blocked on the reporter bridge
        (:class:`~agentdeck.core.reporting.SyncReporter`) needs that very loop free to accept its
        marshaled report. Draining through ``asyncio.gather`` first keeps the loop live; the final
        ``shutdown(wait=True)`` returns immediately once nothing is left to join.
        """
        self._pool.shutdown(wait=False, cancel_futures=True)
        running = [future for future in self._pending if not future.done()]
        if running:
            await asyncio.gather(*(asyncio.wrap_future(future) for future in running), return_exceptions=True)
        self._pool.shutdown(wait=True)


__all__ = ["SyncToolWorkers"]
