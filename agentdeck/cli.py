"""``agentdeck runs signal <run_id> <cancel|pause|resume>`` — the second terminal's way to
reach a run's ``ControlPort`` from a different OS process than the one streaming it.

    agentdeck runs signal <run_id> cancel --control-db path/to/control.sqlite3 --reason "typo"

A recorded ``resume`` here only lifts a pause that has not landed yet: continuing a run that
already stopped means playing it on, which needs the event log and so belongs to a process
holding a Runtime (``App.resume_run``, ``POST /runs/{id}/resume``), not to this file.

A top-level composition root, like ``serve.py``: it wires the SQLite ``ControlPort``
adapter directly, which is why it lives outside ``surfaces/`` — surfaces never import an
adapter (they get one handed to them). There is no HTTP control route (out of scope for
M0) and no registry of which run lives where; the caller already has ``run_id`` from the
stream it was watching.
"""

from __future__ import annotations

import argparse
import asyncio

from agentdeck.adapters.control.sqlite import SqliteControlPort
from agentdeck.core.control import Signal


def build_parser() -> argparse.ArgumentParser:
    """The command tree, split out from :func:`main` so ``scripts/generate_docs_reference.py``
    can walk it (subparsers, arguments, help text) without also running ``main``'s side effect
    of dispatching a real control signal.
    """
    parser = argparse.ArgumentParser(prog="agentdeck")
    subcommands = parser.add_subparsers(dest="resource", required=True)
    runs = subcommands.add_parser("runs")
    runs_commands = runs.add_subparsers(dest="action", required=True)
    signal_cmd = runs_commands.add_parser("signal")
    signal_cmd.add_argument("run_id", help="the run to signal")
    signal_cmd.add_argument(
        "signal", choices=[sig.value for sig in Signal], help="the verb — see Run Control for what each does"
    )
    signal_cmd.add_argument("--control-db", required=True, help="path to the ControlPort's SQLite file")
    signal_cmd.add_argument("--reason", help="why, recorded in the run's log with the request")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    control = SqliteControlPort(args.control_db)
    asyncio.run(control.signal(args.run_id, Signal(args.signal), args.reason))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_parser", "main"]
