"""Five concurrency races run across two real OS processes sharing one SQLite event store.

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
import json
import os
import subprocess
import sys
import time
from collections import Counter
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

# The one knob the staleness half of this file drives, by the name an operator would use.
_STALE_ENV = "AGENTDECK_RUNTIME_STALE_RUN_AFTER_SECONDS"

# Generous on purpose: it is a wedge detector, not a performance budget. Every race below
# finishes in a couple of seconds when it is behaving.
WORKER_TIMEOUT = 180.0

RESUME_TRIALS = 20
CANCEL_TRIALS = 22  # half raced, half ordered the other way round — see the worker's own note
SESSION_TRIALS = 10


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


def _takeover_successor(root: Path, stale_after: str | None) -> subprocess.CompletedProcess[str]:
    """One restart over the killed process's log, with the staleness window set the way an
    operator sets it — through the environment — so the setting itself is under test too."""
    env = {**os.environ}
    if stale_after is None:
        env.pop(_STALE_ENV, None)
    else:
        env[_STALE_ENV] = stale_after
    done = subprocess.run(
        [sys.executable, "-u", str(WORKER), "takeover", "successor", "1", str(root)],
        capture_output=True,
        text=True,
        timeout=WORKER_TIMEOUT,
        check=False,
        env=env,
    )
    assert done.returncode == 0, done.stderr
    return done


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


def _claim_windows(root: Path) -> dict[str, list[tuple[int, int]]]:
    """Every claim attempt, keyed by whatever it contended for — a run for a resume, a session
    for a start — as the (start, end) wall-clock nanoseconds the worker recorded around it."""
    windows: dict[str, list[tuple[int, int]]] = {}
    for line in worker.windows_file(root).read_text().splitlines():
        contended, start, end = line.split()
        windows.setdefault(contended, []).append((int(start), int(end)))
    return windows


def _overlap(windows: Sequence[tuple[int, int]]) -> bool:
    """Whether both peers were inside the same claim at the same moment."""
    return max(start for start, _ in windows) < min(end for _, end in windows)


def _assert_run_is_coherent(events: Sequence[Event], label: str) -> None:
    """The contract suite's own two invariants, on a log two processes produced."""
    assert check_contiguous(events) == [], f"{label}: seq gaps {check_contiguous(events)}\n{_dump(events)}"
    assert [event.seq for event in events] == list(range(len(events))), f"{label}: seq is not 0..n\n{_dump(events)}"
    assert check_terminal(events) is None, f"{label}: {check_terminal(events)}\n{_dump(events)}"


def _wait_for(path: Path, process: subprocess.Popen[str], timeout: float = WORKER_TIMEOUT) -> None:
    """Block until the other process touches ``path``.

    Watching it exit as well as the file: a process that dies on the way to its gate will
    never touch anything, and waiting out the full timeout for that turns a plain crash into
    a three-minute mystery.
    """
    deadline = time.monotonic() + timeout
    while not path.exists():
        if process.poll() is not None:
            _, stderr = process.communicate()
            raise AssertionError(f"exited {process.returncode} before touching {path.name}\n{stderr}")
        if time.monotonic() >= deadline:
            raise AssertionError(f"{path.name} never appeared: the other process never reached its gate")
        time.sleep(0.01)


def test_two_processes_resuming_one_interrupt_leave_one_winner_and_one_node_b_execution(tmp_path: Path) -> None:
    """Two servers answer the same interrupt at the same instant. The store's conditional
    append is the only arbiter, so exactly one may play the approval node, exactly one
    terminal event may land, and the loser must exit 0 having yielded nothing at all."""
    reported = _run_peers("resume", RESUME_TRIALS, tmp_path)
    marks = worker.marks_file(tmp_path).read_text().split()
    attempts = _claim_windows(tmp_path)
    logs = _read_logs(tmp_path, [worker.resume_log_key(trial) for trial in range(RESUME_TRIALS)])
    won: Counter[str] = Counter()
    raced = 0

    for trial in range(RESUME_TRIALS):
        log = logs[worker.resume_log_key(trial)]
        windows = attempts[worker.resume_run_id(trial)]
        assert len(windows) == len(TAGS), f"trial {trial}: {len(windows)} claims, both peers must attempt one"
        raced += _overlap(windows)
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

    # Overlapping claims, not the winner split, are what says this raced: a real race can
    # legitimately go 20/0 on a loaded box (19/1 has been measured against 3-16 idle), while
    # a loser whose claim starts after the winner's finished would lose for the wrong reason
    # and stop catching a claim that checks before it appends.
    assert raced, (
        f"no trial had both peers inside claim_resume at once ({dict(won)} won): the barrier "
        "released them apart, so nothing here contended"
    )
    print(f"double-resume over {RESUME_TRIALS} trials: winners {dict(won)}, {raced} genuinely overlapping")


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


def test_two_processes_starting_a_turn_on_one_session_leave_one_run_and_one_conversation(tmp_path: Path) -> None:
    """Two servers open a turn of one session at the same instant. The store's conditional
    append is the only arbiter, so exactly one turn may run and the other must be refused by
    name — and the engine's own session must end up holding that one turn's conversation, with
    no trace of the refused one. That last assertion is the point of the rule: the log could
    survive two turns, a conversation cannot.
    """
    reported = _run_peers("session", SESSION_TRIALS, tmp_path)
    attempts = _claim_windows(tmp_path)
    logs = _read_logs(tmp_path, [worker.session_log_key(trial) for trial in range(SESSION_TRIALS)])
    won: Counter[str] = Counter()
    raced = 0

    for trial in range(SESSION_TRIALS):
        log = logs[worker.session_log_key(trial)]
        windows = attempts[worker.session_log_key(trial)]
        assert len(windows) == len(TAGS), f"trial {trial}: {len(windows)} claims, both peers must attempt one"
        raced += _overlap(windows)
        outcome = {tag: _kinds(reported, tag, trial) for tag in TAGS}
        winners = [tag for tag, kinds in outcome.items() if kinds != [worker.REFUSED]]

        assert len(winners) == 1, f"trial {trial}: {winners or 'nobody'} ran\n{_dump(log)}"
        winner, loser = winners[0], worker.PEER[winners[0]]
        won[winner] += 1
        assert outcome[winner][0] == "run.started", f"trial {trial}: {outcome}\n{_dump(log)}"
        assert outcome[winner][-1] == "run.completed", f"trial {trial}: {outcome}\n{_dump(log)}"

        assert {event.run_id for event in log} == {worker.session_run_id(trial, winner)}, _dump(log)
        _assert_run_is_coherent(log, f"trial {trial}")
        assert [event.kind for event in log] == outcome[winner], f"trial {trial}\n{_dump(log)}"

        state = json.dumps(worker.session_items(tmp_path, trial))
        assert state.count(worker.turn_input(winner)) == 1, f"trial {trial}: {state}"
        assert worker.turn_input(loser) not in state, f"trial {trial}: the refused turn reached the session\n{state}"
        assert state.count(worker.chunk_text()) == 1, f"trial {trial}: one answer, once: {state}"

    # As in the resume race: overlapping claims are what says this contended, not the split.
    assert raced, (
        f"no trial had both peers inside claim_start at once ({dict(won)} won): the barrier "
        "released them apart, so nothing here contended"
    )
    print(f"one-turn-per-session over {SESSION_TRIALS} trials: winners {dict(won)}, {raced} genuinely overlapping")


def test_a_session_a_killed_run_left_open_is_refused_until_the_staleness_window_passes(tmp_path: Path) -> None:
    """A SIGKILL is the one exit that leaves a run open — every other closes its run in the
    log — so the session that run holds must neither be lost for good nor handed over the
    instant somebody asks: a live turn that has been quiet for a moment would then lose its
    session to a double-clicked send.

    So both halves, in two fresh processes over the dead one's log: refused while the window
    stands, through once it has passed, with the abandoned run closed as ``run.failed`` under
    its own name and the takeover on the successor's stderr where an operator would find it.
    """
    (tmp_path / "sync").mkdir()
    victim = _spawn("takeover", "victim", 1, tmp_path)
    try:
        _wait_for(tmp_path / "sync" / "mid", victim)
        victim.kill()
        victim.communicate(timeout=WORKER_TIMEOUT)
    finally:
        if victim.poll() is None:
            victim.kill()

    refused = _takeover_successor(tmp_path, stale_after=None)
    assert refused.stdout.split()[2:] == [worker.REFUSED], refused.stdout
    still_held = _read_logs(tmp_path, [worker.TAKEOVER_LOG])[worker.TAKEOVER_LOG]
    assert {event.run_id for event in still_held} == {worker.TAKEOVER_KILLED}, _dump(still_held)
    assert len(still_held) == worker.TAKEOVER_STALL_AFTER, _dump(still_held)
    assert check_terminal(still_held) == "no terminal event", _dump(still_held)

    taken = _takeover_successor(tmp_path, stale_after="0")
    assert "took it over and closed it as failed" in taken.stderr, taken.stderr

    log = _read_logs(tmp_path, [worker.TAKEOVER_LOG])[worker.TAKEOVER_LOG]
    abandoned = [event for event in log if event.run_id == worker.TAKEOVER_KILLED]
    next_turn = [event for event in log if event.run_id == worker.TAKEOVER_NEXT]

    assert [event.kind for event in next_turn] == taken.stdout.split()[2:], f"{taken.stdout}\n{_dump(log)}"
    _assert_run_is_coherent(next_turn, "the turn that took the session over")
    assert next_turn[-1].kind == "run.completed", _dump(log)

    _assert_run_is_coherent(abandoned, "the abandoned run")
    assert len(abandoned) == worker.TAKEOVER_STALL_AFTER + 1, _dump(log)
    assert abandoned[-1].kind == "run.failed", _dump(log)
    assert abandoned[-1].payload.error_code == "cancelled_hard", _dump(log)
    # Its own name on it: the run that failed, not the turn that closed it.
    assert abandoned[-1].origin == abandoned[0].origin, _dump(log)
    assert status_of(abandoned) is RunStatus.FAILED, _dump(log)


def test_a_restart_continues_a_killed_processs_log_without_resetting_seq(tmp_path: Path) -> None:
    """One process suspends a run, opens a second one, and is SIGKILLed with that second run
    open mid-stream. A fresh process on the same log resumes the suspended run — ``seq``
    picking up where the dead process left it, never restarting at 0 — while the run that
    died stays exactly as truncated as it really was, with no terminal event invented for it.
    """
    (tmp_path / "sync").mkdir()
    victim = _spawn("restart", "victim", 1, tmp_path)
    try:
        _wait_for(tmp_path / "sync" / "mid", victim)
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

    logs = _read_logs(tmp_path, [worker.RESTART_LOG, worker.RESTART_KILLED])
    log = logs[worker.RESTART_LOG]
    resumed = [event for event in log if event.run_id == worker.RESTART_SUSPENDED]
    # A log of its own, because the victim's suspended run still holds the session and one
    # session takes one turn at a time: a second run there would have been refused, not killed.
    killed = logs[worker.RESTART_KILLED]

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
