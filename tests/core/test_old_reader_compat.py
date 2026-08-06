"""D8, measured rather than asserted: `origin/dev`'s own parser reading this branch's events.

A schema change is called additive by *arguing* that an old reader tolerates it. #107 shipped a
break of exactly that class because the argument was never run. So this loads the released
reader — `agentdeck/core/events.py` and `core/status.py` as they stand on `origin/dev`, straight
out of git — and hands it the new kinds. The bar is the promise the schema makes: an unknown kind
parses as `UnknownEvent`, keeps its raw payload, and moves nothing.

`core/content.py` resolves to *this* branch's copy, because the old modules import it by name.
That is exactly the coverage wanted: this PR does not touch content, so the only difference
between the two readers is the schema change under test.

Skipped, loudly, when `origin/dev` is not fetched — a shallow CI clone has no other ref to
compare against, and a test that quietly invents one would measure nothing.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from types import ModuleType

OLD_REF = "origin/dev"


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
    # Registered before exec: pydantic resolves this module's own postponed annotations through
    # sys.modules, and a model class defined in a module it cannot find does not build.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def old_reader(tmp_path_factory) -> tuple[ModuleType, ModuleType]:
    tmp_path = tmp_path_factory.mktemp("old_reader")
    events = _module_from(OLD_REF, "agentdeck/core/events.py", "old_core_events", tmp_path)
    status = _module_from(OLD_REF, "agentdeck/core/status.py", "old_core_status", tmp_path)
    return events, status


def _wire(event) -> dict[str, Any]:
    return json.loads(event.model_dump_json())


def test_the_old_reader_does_not_already_know_these_kinds(old_reader) -> None:
    """The measurement only means something if the reader really is older. If this fails, the
    ref moved and the kinds landed — rebase, don't relax it."""
    events, _ = old_reader
    assert {"status.reported", "progress.reported"}.isdisjoint(events.KNOWN_KINDS)


@pytest.mark.parametrize("kind", ["status.reported", "progress.reported"])
def test_the_old_reader_parses_a_report_as_an_unknown_event(old_reader, examples, kind) -> None:
    events, _ = old_reader
    parsed = events.parse_event(_wire(examples[kind]))

    assert isinstance(parsed.payload, events.UnknownEvent)
    assert parsed.kind == kind
    assert parsed.payload.raw_payload == _wire(examples[kind])["payload"]  # nothing lost
    assert parsed.seq == examples[kind].seq and parsed.tenant == "acme"  # envelope still read


def test_the_old_reader_folds_a_reporting_run_to_the_same_status(old_reader, examples, make_event) -> None:
    """The half that actually breaks deployments: a kind an old reader mis-folds changes what it
    believes about a live run. Both readers must call this run RUNNING and still open."""
    events, status = old_reader
    kinds = ["run.started", "status.reported", "progress.reported"]
    log = [events.parse_event(_wire(make_event(examples[kind].payload, seq))) for seq, kind in enumerate(kinds)]

    assert status.status_of(log) is status.RunStatus.RUNNING
    assert events.check_terminal(log) == "no terminal event"
    assert events.check_contiguous(log) == []
    assert {"status.reported", "progress.reported"}.isdisjoint(status.LIFECYCLE_KINDS | events.TERMINAL_KINDS)


def test_the_old_reader_skips_them_and_reads_the_rest_of_the_stream(old_reader, examples, make_event) -> None:
    """A consumer's loop, played on the old reader: the reports are skipped, the run still
    completes, and the text still assembles."""
    events, status = old_reader
    kinds = ["run.started", "status.reported", "text.delta", "progress.reported", "run.completed"]
    log = [events.parse_event(_wire(make_event(examples[kind].payload, seq))) for seq, kind in enumerate(kinds)]

    unknown = [event for event in log if isinstance(event.payload, events.UnknownEvent)]
    assert [event.kind for event in unknown] == ["status.reported", "progress.reported"]
    assert [event.payload.text for event in log if event.kind == "text.delta"] == ["Tuesday "]
    assert status.status_of(log) is status.RunStatus.COMPLETED
    assert events.check_terminal(log) is None
