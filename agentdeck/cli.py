"""``agentdeck runs signal <run_id> <cancel|pause|resume>``  -  the second terminal's way to
reach a run's ``ControlPort`` from a different OS process than the one streaming it.

    agentdeck runs signal <run_id> cancel --control-db path/to/control.sqlite3 --reason "typo"

A recorded ``resume`` only lifts a pause that has not landed yet; replaying an already-stopped
run needs the event log and belongs to a process holding a Runtime (``Deck.runs.resume``), not
here. This file wires the SQLite ``ControlPort`` adapter directly rather than through a binding,
which gets one handed to it instead; there is no HTTP control route (out of scope for M0).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os

from agentdeck.adapters.control.sqlite import SqliteControlPort
from agentdeck.core.control import Signal


def build_parser() -> argparse.ArgumentParser:
    """The command tree, split out from :func:`main` so ``scripts/generate_docs_reference.py``
    can walk it (subparsers, arguments, help text) without also running ``main``'s side effect
    of dispatching a real control signal.
    """
    parser = argparse.ArgumentParser(prog="agentdeck")
    subcommands = parser.add_subparsers(dest="resource", required=True)
    subcommands.add_parser("chat", help="a one-process terminal client over Terminal.stdio()")
    runs = subcommands.add_parser("runs")
    runs_commands = runs.add_subparsers(dest="action", required=True)
    signal_cmd = runs_commands.add_parser("signal")
    signal_cmd.add_argument("run_id", help="the run to signal")
    signal_cmd.add_argument(
        "signal", choices=[sig.value for sig in Signal], help="the verb  -  see Run Control for what each does"
    )
    signal_cmd.add_argument("--control-db", required=True, help="path to the ControlPort's SQLite file")
    signal_cmd.add_argument("--reason", help="why, recorded in the run's log with the request")
    return parser


def _chat() -> int:
    """``Deck.from_project().serve(Terminal.stdio())``  -  a stdio-only ``Exposure``, so
    ``serve()`` never imports uvicorn (``exposure.py``). Mid-run Ctrl-C surfaces here as
    ``KeyboardInterrupt``; idle Ctrl-C raises nothing (``terminal/binding.py``). ``os._exit``
    covers both: the stdio loop's blocked ``stdin.readline()`` runs in a worker thread a live
    terminal never gives an EOF, so normal shutdown would hang joining it.
    """
    from agentdeck.adapters.bindings.terminal import Terminal
    from agentdeck.deck import Deck

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(Deck.from_project().serve(Terminal.stdio()))
    os._exit(0)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.resource == "chat":
        return _chat()
    control = SqliteControlPort(args.control_db)
    # A run's id is minted and canonical, so this CLI's argument addresses one directly  -  no
    # namespace to combine it with, no resolution step, and nothing to derive.
    asyncio.run(control.signal(args.run_id, Signal(args.signal), args.reason))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_parser", "main"]
