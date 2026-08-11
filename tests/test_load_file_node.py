"""``LoadFileNode`` reads the host filesystem, and only by absolute path.

The relative branch used to go through the sandbox port. Nothing bound a sandbox, so it
raised whatever the port raised; sandboxing then left v3 with the port. The node has to keep
refusing rather than silently resolving against the process cwd — that would widen exactly
what the sandbox narrowed — so both halves are asserted here.
"""

from __future__ import annotations

import pytest

from agentdeck.authoring import LoadFileNode


async def test_an_absolute_path_is_read_from_the_host_filesystem(tmp_path):
    target = tmp_path / "brief.txt"
    target.write_text("ship it", encoding="utf-8")
    node = LoadFileNode(path=lambda state: state["file"], into="brief")

    assert await node({"file": str(target)}) == {"brief": "ship it"}


async def test_a_relative_path_is_refused_and_reads_nothing(tmp_path, monkeypatch):
    """Named as the failure it is, and *not* resolved against the cwd — the file sitting
    right there at the relative path must stay unread."""
    (tmp_path / "brief.txt").write_text("cwd contents", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    node = LoadFileNode(path=lambda state: state["file"], into="brief")

    with pytest.raises(RuntimeError, match="absolute path"):
        await node({"file": "brief.txt"})


async def test_a_falsy_path_is_a_no_op():
    node = LoadFileNode(path=lambda state: state.get("file"), into="brief")

    assert await node({}) == {}


async def test_parse_is_applied_to_what_was_read(tmp_path):
    target = tmp_path / "count.txt"
    target.write_text("42", encoding="utf-8")
    node = LoadFileNode(path=lambda state: state["file"], into="count", parse=int)

    assert await node({"file": str(target)}) == {"count": 42}


def test_a_static_path_string_is_refused_at_construction():
    with pytest.raises(TypeError, match="callable"):
        LoadFileNode(path="/tmp/brief.txt", into="brief")  # type: ignore[arg-type]
