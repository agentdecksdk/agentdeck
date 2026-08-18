"""Two model configs, and why they differ.

``extra="ignore"`` is what forward compatibility costs on a model something *parses*: a payload
written by a newer version has to land, not raise. A model only ever built in-process gets no
such payload  -  there, an unknown keyword is a typo, and ignoring it defaults the field the caller
thought they were setting.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agentdeck.core.events import Event, TextDelta
from agentdeck.core.invocable import InvocableKind, InvocableSpec
from agentdeck.core.ports.tools import ToolSet


def test_a_toolset_refuses_a_keyword_it_does_not_have():
    """The failure this prevents is silent: ``tools`` dropped leaves an empty set, and the run
    then degrades exactly like an unreachable tool source  -  the one thing ToolSet exists to
    make visible."""
    with pytest.raises(ValidationError):
        ToolSet(tolls=(object(),))  # ty: ignore[unknown-argument]  -  the typo is the test


def test_an_invocable_spec_refuses_a_keyword_it_does_not_have():
    with pytest.raises(ValidationError):
        InvocableSpec(name="a", kind=InvocableKind.AGENT, engine="stub", metadta={})  # ty: ignore[unknown-argument]


def test_a_payload_still_ignores_a_field_a_newer_writer_added():
    """The other half of the bargain: this is the wire, and a reader that raises here cannot
    read a log a newer version wrote."""
    parsed = TextDelta.model_validate({"message_id": "m-1", "text": "hi", "cadence": "fast"})
    assert parsed == TextDelta(message_id="m-1", text="hi")


def test_an_envelope_still_ignores_a_field_a_newer_writer_added():
    written = Event(
        event_id="e-1",
        run_id="r-1",
        seq=0,
        ts=datetime(2026, 1, 1, tzinfo=UTC),
        namespace="acme",
        session_id=None,
        kind="text.delta",
        origin="engine",
        payload=TextDelta(message_id="m-1", text="hi"),
    ).model_dump(mode="json")
    assert Event.model_validate({**written, "invented_later": True}) == Event.model_validate(written)
