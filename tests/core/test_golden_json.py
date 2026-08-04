"""One frozen serialization per kind. A diff in ``snapshots/`` IS a schema change.

Re-record with ``AGENTDECK_GOLDEN_UPDATE=1 pytest tests/core`` only after deciding the
new shape is correct, and say so in the PR description.
"""

from __future__ import annotations

import os
from pathlib import Path

SNAPSHOTS = Path(__file__).parent / "snapshots"
UPDATE = os.getenv("AGENTDECK_GOLDEN_UPDATE") == "1"


def test_serialization_matches_snapshots(examples):
    recorded = {f"{kind}.json": (event.model_dump_json(indent=2) + "\n").encode() for kind, event in examples.items()}
    if UPDATE:
        SNAPSHOTS.mkdir(exist_ok=True)
        for stale in {p.name for p in SNAPSHOTS.iterdir()} - set(recorded):
            (SNAPSHOTS / stale).unlink()  # a renamed kind must not leave its old file behind
        for name, body in recorded.items():
            (SNAPSHOTS / name).write_bytes(body)
        return
    for name, body in recorded.items():
        assert body == (SNAPSHOTS / name).read_bytes(), f"schema changed: {name}"
    assert sorted(recorded) == sorted(p.name for p in SNAPSHOTS.iterdir())
