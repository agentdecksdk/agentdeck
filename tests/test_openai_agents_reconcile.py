"""``reconcile``'s two text extractors, log side and session side, must read the same string
off a multi-block turn (#161)  -  the log side joins ``TextBlock``s, the session side joins the
canonical parts a live multimodal turn would have written into the SDK session. Before
``_to_sdk_input`` could emit a parts list, the two agreed by accident: the session's content
was always the bare string the log side had already joined. This pins that they still agree
once the session can hold a list of typed parts instead.

Issue #226: a ``DataBlock`` renders as an ``input_text`` part too, so it joins ``TextBlock`` on
the log side (via ``render_data_block``) rather than being silently dropped from the
comparison  -  dropping it would make any turn that carried one read as a permanent divergence.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from agentdeck.adapters.engines.openai_agents.reconcile import _item_text, _text_of, reconcile, render_data_block
from agentdeck.core.content import DataBlock, ImageBlock, TextBlock
from agentdeck.core.events import Event, RunStarted

TS = datetime(2026, 1, 1, tzinfo=UTC)


class _FakeSession:
    """The three ``Session`` protocol members ``reconcile`` actually calls."""

    def __init__(self, session_id: str, items: list[dict[str, Any]]) -> None:
        self.session_id = session_id
        self._items = items
        self.added: list[dict[str, Any]] = []

    async def get_items(self, limit: int | None = None) -> list[dict[str, Any]]:
        return self._items

    async def add_items(self, items: list[dict[str, Any]]) -> None:
        self.added.extend(items)
        self._items.extend(items)


def _started(input: list[Any], seq: int = 0) -> Event:
    return Event(
        kind="run.started",
        seq=seq,
        run_id="run_1",
        session_id="sess_1",
        namespace="acme",
        origin="Greeter",
        ts=TS,
        payload=RunStarted(invocable="Greeter", kind_of_invocable="agent", input=input),
    )


def test_text_of_and_item_text_agree_on_a_multi_text_block_turn():
    blocks = [TextBlock(text="a"), TextBlock(text="b")]
    parts = [{"type": "input_text", "text": "a"}, {"type": "input_text", "text": "b"}]
    assert _text_of(blocks) == _item_text(parts) == "a\nb"


def test_item_text_skips_non_text_parts_instead_of_joining_in_an_empty_gap():
    parts = [
        {"type": "input_text", "text": "a"},
        {"type": "input_image", "image_url": "data:image/png;base64,AA=="},
        {"type": "input_text", "text": "b"},
    ]
    assert _item_text(parts) == "a\nb"


async def test_reconcile_sees_no_divergence_for_a_text_image_text_turn():
    """The scenario #161 could break silently: a live turn's canonical parts land in the
    session, and the *next* turn's reconcile must not read that as a disagreement with the log."""
    blocks = [TextBlock(text="a"), ImageBlock(media_type="image/png", data_b64="AA=="), TextBlock(text="b")]
    session_item = {
        "role": "user",
        "content": [
            {"type": "input_text", "text": "a"},
            {"type": "input_image", "image_url": "data:image/png;base64,AA=="},
            {"type": "input_text", "text": "b"},
        ],
    }
    session = _FakeSession("sess_1", [session_item])

    result = await reconcile(session, [_started(blocks)])

    assert result is None
    assert session.added == []  # already in agreement, nothing to repair


def test_data_block_renders_the_same_text_on_both_sides():
    """The exact scenario #226 introduced: a ``DataBlock`` now produces an ``input_text`` part
    indistinguishable from a real ``TextBlock``'s, so the log side has to render it identically
    to ``engine._part_of`` or the two transcripts never agree."""
    block = DataBlock(data={"page": "reference/deck"})
    assert _text_of([block]) == render_data_block(block) == '{"page": "reference/deck"}'


async def test_reconcile_sees_no_divergence_for_a_turn_carrying_a_data_block():
    """Without a matching renderer on the log side, this scenario used to report a spurious
    ``session_diverged`` on every turn after the one that carried a ``DataBlock``  -  the session
    stored its rendered JSON as a plain ``input_text`` part, indistinguishable from a
    ``TextBlock``'s, and the log side used to drop the block entirely instead of rendering it."""
    blocks = [TextBlock(text="what page am I on?"), DataBlock(data={"page": "reference/deck"})]
    session_item = {
        "role": "user",
        "content": [
            {"type": "input_text", "text": "what page am I on?"},
            {"type": "input_text", "text": '{"page": "reference/deck"}'},
        ],
    }
    session = _FakeSession("sess_1", [session_item])

    result = await reconcile(session, [_started(blocks)])

    assert result is None
    assert session.added == []  # already in agreement, nothing to repair
