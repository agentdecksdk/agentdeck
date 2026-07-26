"""The one batch-with-bisect runner every batching LLM skill shares.

Was copy-pasted as ``_chunk`` / ``judge_with_bisect`` in three skills: run items
K-per-call concurrently; a batch that raises after its own retries is **bisected**
and each half retried, recursively down to a singleton. A singleton uses the
proven per-item ``one_call`` when given, else ``batch_call([item])``. So batching
can never do worse than one-call-per-item — it only collapses calls when the batch
succeeds.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")

Results = list[tuple[T, R]]
Failures = list[tuple[str, str]]


async def map_batched(
    items: Sequence[T],
    *,
    key: Callable[[T], str],
    batch_call: Callable[[list[T]], Awaitable[dict[str, R]]],
    one_call: Callable[[T], Awaitable[R]] | None = None,
    concurrency: int = 8,
    batch_size: int = 1,
) -> tuple[Results[T, R], Failures]:
    """Map ``items`` through ``batch_call`` with concurrency + bisect-on-failure.

    ``key`` maps an item to the id ``batch_call``'s ``{id: result}`` is keyed by.
    Returns ``(results, failures)``: ``results`` are ``(item, result)`` pairs and
    ``failures`` are ``(key, error)`` for items that still failed on their own.
    """
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _one(item: T) -> tuple[Results[T, R], Failures]:
        """Proven per-item path — the floor a failing batch bisects down to."""
        async with sem:
            try:
                result = await one_call(item) if one_call is not None else (await batch_call([item]))[key(item)]
                return [(item, result)], []
            except Exception as exc:  # one item failing must not abort the run
                return [], [(key(item), str(exc))]

    async def _chunk(chunk: list[T]) -> tuple[Results[T, R], Failures]:
        if len(chunk) <= 1:
            return await _one(chunk[0]) if chunk else ([], [])
        try:
            async with sem:  # hold the slot only for the actual call, never across recursion
                grouped = await batch_call(chunk)
            return [(it, grouped[key(it)]) for it in chunk], []
        except Exception:  # a failed batch is bisected, not abandoned
            mid = len(chunk) // 2
            left, right = await asyncio.gather(_chunk(chunk[:mid]), _chunk(chunk[mid:]))
            return left[0] + right[0], left[1] + right[1]

    size = max(1, batch_size)
    chunks = [list(items[i : i + size]) for i in range(0, len(items), size)]
    parts = await asyncio.gather(*(_chunk(c) for c in chunks))
    results: Results[T, R] = [r for part in parts for r in part[0]]
    failures: Failures = [f for part in parts for f in part[1]]
    return results, failures


__all__ = ["map_batched"]
