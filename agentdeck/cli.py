"""``agentdeck runs signal <run_id> cancel`` — the second terminal's way to reach a run's
``ControlPort`` from a different OS process than the one streaming it.

    agentdeck runs signal <run_id> cancel --control-db path/to/control.sqlite3

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
from agentdeck.core.ports.control import Signal


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentdeck")
    subcommands = parser.add_subparsers(dest="resource", required=True)
    runs = subcommands.add_parser("runs")
    runs_commands = runs.add_subparsers(dest="action", required=True)
    signal_cmd = runs_commands.add_parser("signal")
    signal_cmd.add_argument("run_id")
    signal_cmd.add_argument("signal", choices=[sig.value for sig in Signal])
    signal_cmd.add_argument("--control-db", required=True, help="path to the ControlPort's SQLite file")

    args = parser.parse_args(argv)
    control = SqliteControlPort(args.control_db)
    asyncio.run(control.signal(args.run_id, Signal(args.signal)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
