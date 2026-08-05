"""Four concurrency races run across two real OS processes sharing one SQLite event store.

Every invariant asserted here is the contract suite's own — ``check_contiguous`` and
``check_terminal`` — applied to logs that two processes wrote together. That is the whole
point of the file: each guarantee was proven inside one process, while the shape a
deployment runs in is N servers agreeing through a file. Nothing in one process here can
see the other, so a guarantee that only held because of a process-local lock fails.

The workers live in ``concurrency_worker.py``. They synchronize through files rather than
sleeps, so both sides enter a race at the same instant instead of two timelines being
hoped to overlap; the three races whose outcome is then decided by timing repeat many
times, UC3-style, because a race that passes once proves nothing. Every process gets a
subprocess timeout and every trial an ``asyncio.timeout``, so a wedge fails loudly instead
of hanging CI. Failures print the offending log in full, and the observed split of a race
is printed even when it passes: these are the tests that catch a real regression, and an
``assert`` on its own would not be enough to diagnose one.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from collections import Counter
from itertools import groupby
from pathlib import Path
from typing import TYPE_CHECKING

import concurrency_worker as worker

from agentdeck.adapters.stores.sqlite import SqliteEventStore
from agentdeck.core.events import check_contiguous, check_terminal
from agentdeck.core.status import RunStatus, status_of

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agentdeck.core.events import Event

WORKER = Path(__file__).parent / "concurrency_worker.py"
TAGS = ("a", "b")

# Generous on purpose: it is a wedge detector, not a performance budget. Every race below
# finishes in a couple of seconds when it is behaving.
WORKER_TIMEOUT = 180.0

RESUME_TRIALS = 20
CANCEL_TRIALS = 22  # half raced, half ordered the other way round — see the worker's own note
INTERLEAVE_TRIALS = 10


def _dump(events: Sequence[Event]) -> str:
    """The log, one event per line — pasted into every failure message, because a broken
    race is unreadable from the assertion alone."""
    return "\n".join(f"  {event.run_id} seq={event.seq} {event.kind}" for event in events)


def _spawn(race: str, tag: str, trials: int, root: Path) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-u", str(WORKER), race, tag, str(trials), str(root)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _run_peers(race: str, trials: int, root: Path) -> dict[str, list[list[str]]]:
    """Both peers at once; their per-trial report lines back, keyed by tag.

    Collected only after both have exited, which keeps the test out of the race it is
    watching. Safe against a full pipe because a worker prints one short line per trial —
    kilobytes against the pipe's own buffer — and a worker that wedges instead of printing
    is killed by ``communicate``'s timeout rather than waited for forever.
    """
    (root / "sync").mkdir(exist_ok=True)
    peers = {tag: _spawn(race, tag, trials, root) for tag in TAGS}
    reported: dict[str, list[list[str]]] = {}
    try:
        for tag, peer in peers.items():
            stdout, stderr = peer.communicate(timeout=WORKER_TIMEOUT)
            assert peer.returncode == 0, f"{race} peer {tag} exited {peer.returncode}\n{stderr}"
            reported[tag] = [line.split() for line in stdout.splitlines() if line.strip()]
            assert len(reported[tag]) == trials, f"{race} peer {tag} reported {len(reported[tag])} of {trials} trials"
    finally:
        for peer in peers.values():
            if peer.poll() is None:
                peer.kill()
    return reported


def _kinds(reported: dict[str, list[list[str]]], tag: str, trial: int) -> list[str]:
    """One report line's kinds, after checking it is the line it claims to be — a worker
    that skipped a trial must not shift every later assertion by one."""
    line = reported[tag][trial]
    assert line[:2] == [str(trial), tag], f"expected trial {trial} from peer {tag}, got {line}"
    return line[2:]


def _read_logs(root: Path, log_keys: Sequence[str]) -> dict[str, list[Event]]:
    """Read the finished logs through one fresh connection, the way a third server joining
    afterwards would: no object from either worker survives to be consulted."""

    async def _read() -> dict[str, list[Event]]:
        store = SqliteEventStore(worker.events_db(root))
        try:
            return {key: await store.read(key, worker.context("reader")) for key in log_keys}
        finally:
            store.close()

    return asyncio.run(_read())


def _assert_run_is_coherent(events: Sequence[Event], label: str) -> None:
    """The contract suite's own two invariants, on a log two processes produced."""
    assert check_contiguous(events) == [], f"{label}: seq gaps {check_contiguous(events)}\n{_dump(events)}"
    assert [event.seq for event in events] == list(range(len(events))), f"{label}: seq is not 0..n\n{_dump(events)}"
    assert check_terminal(events) is None, f"{label}: {check_terminal(events)}\n{_dump(events)}"


def _wait_for(path: Path, timeout: float = WORKER_TIMEOUT) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() >= deadline:
            raise AssertionError(f"{path.name} never appeared: the other process never reached its gate")
        time.sleep(0.01)


def test_two_processes_resuming_one_interrupt_leave_one_winner_and_one_node_b_execution(tmp_path: Path) -> None:
    """Two servers answer the same interrupt at the same instant. The store's conditional
    append is the only arbiter, so exactly one may play the approval node, exactly one
    terminal event may land, and the loser must exit 0 having yielded nothing at all."""
    reported = _run_peers("resume", RESUME_TRIALS, tmp_path)
    marks = worker.marks_file(tmp_path).read_text().split()
    logs = _read_logs(tmp_path, [worker.resume_log_key(trial) for trial in range(RESUME_TRIALS)])
    won: Counter[str] = Counter()

    for trial in range(RESUME_TRIALS):
        log = logs[worker.resume_log_key(trial)]
        yielded = {tag: _kinds(reported, tag, trial) for tag in TAGS}
        winners = [tag for tag, kinds in yielded.items() if kinds]

        assert len(winners) == 1, f"trial {trial}: {winners or 'nobody'} won\n{_dump(log)}"
        assert yielded[winners[0]] == worker.APPROVED_KINDS, f"trial {trial}: {yielded}\n{_dump(log)}"
        won[winners[0]] += 1
        assert marks.count(worker.resume_run_id(trial)) == 1, (
            f"trial {trial}: the engine played node B {marks.count(worker.resume_run_id(trial))} times\n{_dump(log)}"
        )

        _assert_run_is_coherent(log, f"trial {trial}")
        assert [event.kind for event in log] == [
            "run.started",
            "text.delta",
            "run.interrupted",
            *worker.APPROVED_KINDS,
        ], f"trial {trial}\n{_dump(log)}"

    print(f"double-resume winners over {RESUME_TRIALS} trials: {dict(won)}")


def test_a_cancel_racing_completion_leaves_exactly_one_terminal_event_with_nothing_after_it(tmp_path: Path) -> None:
    """A cancel signal from a second process arrives as the run reaches its last stretch.
    Either side may win it, and what may never happen either way is two terminal events, a
    terminal event with anything after it, or a stream that disagrees with the log.

    On the trials the worker orders rather than races, the winner is known: the run had
    already completed, so the signal must add nothing to a log that is closed.
    """
    reported = _run_peers("cancel", CANCEL_TRIALS, tmp_path)
    logs = _read_logs(tmp_path, [worker.cancel_run_id(trial) for trial in range(CANCEL_TRIALS)])
    winners: Counter[str] = Counter()

    for trial in range(CANCEL_TRIALS):
        log = logs[worker.cancel_run_id(trial)]
        assert _kinds(reported, "b", trial) == ["signalled"]

        _assert_run_is_coherent(log, f"trial {trial}")
        assert [event.kind for event in log] == _kinds(reported, "a", trial), (
            f"trial {trial}: the running process saw a different stream than the log holds\n{_dump(log)}"
        )
        assert log[-1].kind in {"run.completed", "run.cancelled"}, f"trial {trial}\n{_dump(log)}"
        assert status_of(log) in {RunStatus.COMPLETED, RunStatus.CANCELLED}, f"trial {trial}\n{_dump(log)}"
        if not worker.cancel_is_racing(trial):
            assert log[-1].kind == "run.completed", (
                f"trial {trial}: the signal was written after this run finished\n{_dump(log)}"
            )
            assert status_of(log) is RunStatus.COMPLETED, f"trial {trial}\n{_dump(log)}"
        winners[log[-1].kind] += 1

    ordered = sum(1 for trial in range(CANCEL_TRIALS) if not worker.cancel_is_racing(trial))
    assert winners["run.completed"] >= ordered  # every ordered trial, plus any race completion won
    # Which side wins a genuine race is the coin toss under test, so it is reported rather
    # than asserted — a run counted here closed exactly once either way.
    print(f"cancel-vs-completion over {CANCEL_TRIALS} trials ({ordered} ordered): {dict(winners)}")


def test_two_processes_running_different_runs_on_one_log_keep_each_runs_seq_contiguous(tmp_path: Path) -> None:
    """One session log, two runs, two processes appending into it at once. ``seq`` is per
    run, so each run's must still be contiguous from 0 and closed by exactly one terminal
    event however the two runs' writes ended up interleaved in the file."""
    reported = _run_peers("interleave", INTERLEAVE_TRIALS, tmp_path)
    logs = _read_logs(tmp_path, [worker.interleave_log_key(trial) for trial in range(INTERLEAVE_TRIALS)])
    interleaved = 0

    for trial in range(INTERLEAVE_TRIALS):
        log = logs[worker.interleave_log_key(trial)]
        expected = {worker.interleave_run_id(trial, tag): _kinds(reported, tag, trial) for tag in TAGS}
        per_run: dict[str, list[Event]] = {run_id: [] for run_id in expected}
        for event in log:
            assert event.run_id in per_run, f"trial {trial}: stray run {event.run_id}\n{_dump(log)}"
            per_run[event.run_id].append(event)

        for run_id, events in per_run.items():
            _assert_run_is_coherent(events, f"trial {trial} run {run_id}")
            assert [event.kind for event in events] == expected[run_id], f"trial {trial} run {run_id}\n{_dump(log)}"
        assert len(log) == sum(len(events) for events in per_run.values()), f"trial {trial}\n{_dump(log)}"

        # More than two blocks of run_ids means the two processes really were writing into
        # the file at the same time, rather than one finishing before the other started.
        if len(list(groupby(event.run_id for event in log))) > 2:
            interleaved += 1

    assert interleaved, f"no trial interleaved in {INTERLEAVE_TRIALS}: the two runs never actually overlapped"


def test_a_restart_continues_a_killed_processs_log_without_resetting_seq(tmp_path: Path) -> None:
    """One process suspends a run, opens a second one, and is SIGKILLed with that second run
    open mid-stream. A fresh process on the same log resumes the suspended run — ``seq``
    picking up where the dead process left it, never restarting at 0 — while the run that
    died stays exactly as truncated as it really was, with no terminal event invented for it.
    """
    (tmp_path / "sync").mkdir()
    victim = _spawn("restart", "victim", 1, tmp_path)
    try:
        _wait_for(tmp_path / "sync" / "mid")
        victim.kill()
        victim_out, _ = victim.communicate(timeout=WORKER_TIMEOUT)
    finally:
        if victim.poll() is None:
            victim.kill()
    assert victim_out.split()[2:] == ["run.started", "text.delta", "run.interrupted"], victim_out

    successor = subprocess.run(
        [sys.executable, "-u", str(WORKER), "restart", "successor", "1", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=WORKER_TIMEOUT,
    )
    assert successor.returncode == 0, successor.stderr
    lines = [line.split() for line in successor.stdout.splitlines() if line.strip()]

    log = _read_logs(tmp_path, [worker.RESTART_LOG])[worker.RESTART_LOG]
    resumed = [event for event in log if event.run_id == worker.RESTART_SUSPENDED]
    killed = [event for event in log if event.run_id == worker.RESTART_KILLED]

    # Its seq check is the one that matters here: a resume that restarted the counter would
    # give 0,1,2,0,1,2,3 — no gaps to find, and still wrong.
    _assert_run_is_coherent(resumed, "the resumed run")
    assert [event.kind for event in resumed] == [
        "run.started",
        "text.delta",
        "run.interrupted",
        *worker.APPROVED_KINDS,
    ], _dump(log)
    assert lines[0][2:] == worker.APPROVED_KINDS, f"{lines}\n{_dump(log)}"

    # The kill left this run open, and the log says so instead of inventing a close for it.
    assert len(killed) == worker.RESTART_STALL_AFTER, _dump(log)
    assert check_contiguous(killed) == [], _dump(log)
    assert [event.seq for event in killed] == list(range(len(killed))), _dump(log)
    assert check_terminal(killed) == "no terminal event", f"{check_terminal(killed)}\n{_dump(log)}"
    assert status_of(killed) is RunStatus.RUNNING, _dump(log)
    assert lines[1][2:] == [], f"resuming a run that is still RUNNING must be a no-op: {lines}\n{_dump(log)}"
