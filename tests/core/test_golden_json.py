"""One frozen serialization per kind. A diff in ``snapshots/`` IS a schema change.

Re-record with ``AGENTDECK_GOLDEN_UPDATE=1 pytest tests/core`` only after deciding the
new shape is correct, and say so in the PR description.
"""

from __future__ import annotations

import os
from pathlib import Path

from conftest import PAYLOADS, examples_from  # tests/core is not a package; pytest puts it on the path

from agentdeck.core import UnknownEvent

SNAPSHOTS = Path(__file__).parent / "snapshots"
UPDATE = os.getenv("AGENTDECK_GOLDEN_UPDATE") == "1"


def _recorded(events):
    return {f"{kind}.json": (event.model_dump_json(indent=2) + "\n").encode() for kind, event in events.items()}


def _record_into(target: Path, recorded: dict[str, bytes]) -> None:
    target.mkdir(exist_ok=True)
    for stale in {p.name for p in target.glob("*.json")} - set(recorded):
        (target / stale).unlink()  # a renamed kind must not leave its old file behind
    for name, body in recorded.items():
        (target / name).write_bytes(body)


def test_serialization_matches_snapshots(examples):
    recorded = _recorded(examples)
    if UPDATE:
        _record_into(SNAPSHOTS, recorded)
        return
    # set first: a missing or orphaned kind reads as a set diff, not a FileNotFoundError
    assert sorted(recorded) == sorted(p.name for p in SNAPSHOTS.glob("*.json"))
    for name, body in recorded.items():
        assert body == (SNAPSHOTS / name).read_bytes(), f"schema changed: {name}"


def test_adding_a_payload_kind_rewrites_exactly_its_own_snapshot(tmp_path):
    """#121's assertion, made mechanical: adding a kind must cost one new file and disturb none
    of its neighbours, because a snapshot diff is supposed to *be* a schema change.

    Two things make this decisive rather than a restatement of the fixture.

    The extra payload is **prepended**, not appended. Under the old rule  -  ``seq`` taken from the
    payload's index in ``PAYLOADS``  -  appending is the one insertion that happens to be safe, so
    an appended extra would pass against the very fixture this test exists to forbid. Prepending
    shifts every later index, so the regression rewrites all ~21 files and fails here loudly.

    The extra is an ``UnknownEvent``. ``PAYLOADS`` is exhaustive over ``KNOWN_KINDS`` (pinned by
    ``test_every_known_kind_has_an_example``), so there is no spare known kind to add; the
    unknown-kind branch of the union is the only way to mint a genuinely new one without editing
    the schema. It travels the same ``_event`` envelope and the same writer as every real kind.

    Both sides go through ``conftest.examples_from``, which is the fixture's own rule rather than
    a copy of it. Re-deriving the ``seq`` here instead would make the test pass under the exact
    regression it guards.
    """
    newcomer = UnknownEvent(kind="test.newcomer", raw_payload={"hello": "world"})
    before, after = tmp_path / "before", tmp_path / "after"
    _record_into(before, _recorded(examples_from(PAYLOADS)))
    _record_into(after, _recorded(examples_from((newcomer, *PAYLOADS))))

    changed = {
        path.name
        for path in after.glob("*.json")
        if not (before / path.name).exists() or (before / path.name).read_bytes() != path.read_bytes()
    }
    assert changed == {"test.newcomer.json"}, changed
    assert {p.name for p in before.glob("*.json")} - {p.name for p in after.glob("*.json")} == set()
