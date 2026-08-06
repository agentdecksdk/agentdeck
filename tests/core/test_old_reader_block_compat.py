"""D8, measured rather than asserted: the released `ContentBlock` fed a block type it has
never seen.

Same method as `test_old_reader_compat.py`, aimed at the module that change #109 touches
instead of the one that one measures. `agentdeck/core/content.py` is loaded as it stood at
BASELINE — before `UnknownBlock` existed — and handed the wire shape of a block type this
tree's addition introduces. The first bar is the bug #109 exists to fix, run rather than
argued: that old `ContentBlock` is a strict discriminated union, so it must reject the block
outright. The second bar is this tree's fix: the same wire shape, inside a real event, must
parse, keep the raw block, and leave `status_of` and the terminal invariant exactly where they
were without it.

Skipped, loudly, when the baseline is not fetched — see `test_old_reader_compat.py` for why.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import TypeAdapter, ValidationError

from agentdeck.core import UnknownBlock, check_terminal, parse_event
from agentdeck.core.status import RunStatus, status_of

if TYPE_CHECKING:
    from types import ModuleType

BASELINE = "v2.0.0b4"
"""The newest released reader — same tag `test_old_reader_compat.py` measures against."""

# Not one of TextBlock/ImageBlock/ResourceBlock/DataBlock on either side of #109; that is the
# whole point of "a type dev has never seen".
UNFAMILIAR_BLOCK = {"type": "audio", "uri": "s3://clips/9.mp3", "duration_s": 12}

TS = "2026-01-01T12:00:00+00:00"


def _wire(kind: str, payload: dict, seq: int = 0) -> dict[str, Any]:
    return {
        "v": 1,
        "kind": kind,
        "seq": seq,
        "run_id": "run_1",
        "session_id": None,
        "tenant": "acme",
        "origin": "Greeter",
        "ts": TS,
        "payload": {"kind": kind, **payload},
    }


def _module_from(ref: str, path: str, name: str, tmp_path) -> ModuleType:
    """Import one file as it stands on ``ref``, under its own module name."""
    try:
        source = subprocess.run(
            ["git", "show", f"{ref}:{path}"],
            capture_output=True,
            check=True,
            text=True,
            timeout=30,
        ).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        pytest.skip(f"cannot read {path} at {ref}, so the old reader cannot be measured: {exc}")
    file = tmp_path / f"{name}.py"
    file.write_text(source)
    spec = importlib.util.spec_from_file_location(name, file)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def old_content(tmp_path_factory) -> ModuleType:
    tmp_path = tmp_path_factory.mktemp("old_content")
    return _module_from(BASELINE, "agentdeck/core/content.py", "old_core_content", tmp_path)


def test_the_baseline_content_module_has_no_unknown_block(old_content) -> None:
    """The measurement only means something if the reader really predates the fallback. If
    this fails, the baseline moved past #109 — retire the measurement, don't relax it."""
    assert not hasattr(old_content, "UnknownBlock")


def test_the_old_reader_rejects_a_block_type_it_has_never_seen(old_content) -> None:
    """Run, not argued: this is #101's asymmetry as it actually behaves on the released
    parser, the bug #109 exists to close."""
    old_blocks = TypeAdapter(list[old_content.ContentBlock])
    with pytest.raises(ValidationError):
        old_blocks.validate_python([UNFAMILIAR_BLOCK])


def test_this_tree_parses_the_same_block_as_unknown_and_keeps_it_raw() -> None:
    wire = _wire(
        "run.started",
        {
            "invocable": "Greeter",
            "kind_of_invocable": "agent",
            "parent_run_id": None,
            "input": [{"type": "text", "text": "hi"}, UNFAMILIAR_BLOCK],
            "context": {"principal": "user:sagi", "trace_id": "t"},
        },
    )
    event = parse_event(wire)  # must not raise — the fix under test
    assert event.payload.input[1] == UnknownBlock(type="audio", raw_block=UNFAMILIAR_BLOCK)  # raw block kept


def test_this_tree_round_trips_the_event_carrying_it() -> None:
    wire = _wire(
        "run.started",
        {
            "invocable": "Greeter",
            "kind_of_invocable": "agent",
            "parent_run_id": None,
            "input": [UNFAMILIAR_BLOCK],
            "context": {"principal": "user:sagi", "trace_id": "t"},
        },
    )
    event = parse_event(wire)
    assert parse_event(json.loads(event.model_dump_json())) == event


def test_status_of_and_the_terminal_invariant_are_unchanged_by_the_unfamiliar_block() -> None:
    """The measurement that matters per #109: a run whose `run.started.input` carries a block
    type this tree has never seen folds to the same status, and the same terminal verdict, as
    the identical run without it."""
    plain_started = _wire(
        "run.started",
        {
            "invocable": "Greeter",
            "kind_of_invocable": "agent",
            "parent_run_id": None,
            "input": [{"type": "text", "text": "hi"}],
            "context": {"principal": "user:sagi", "trace_id": "t"},
        },
        seq=0,
    )
    odd_started = _wire(
        "run.started",
        {
            "invocable": "Greeter",
            "kind_of_invocable": "agent",
            "parent_run_id": None,
            "input": [{"type": "text", "text": "hi"}, UNFAMILIAR_BLOCK],
            "context": {"principal": "user:sagi", "trace_id": "t"},
        },
        seq=0,
    )
    completed = _wire(
        "run.completed",
        {"output": [{"type": "text", "text": "done"}], "usage": {"input_tokens": 1, "output_tokens": 1}},
        seq=1,
    )

    plain_run = [parse_event(plain_started), parse_event(completed)]
    odd_run = [parse_event(odd_started), parse_event(completed)]

    assert status_of(odd_run) is status_of(plain_run) is RunStatus.COMPLETED
    assert check_terminal(odd_run) is check_terminal(plain_run) is None
