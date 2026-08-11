"""Anti-rot checks for the copyable decks under ``examples/``.

Each example is **built, never run**. ``Deck.build()`` validates the whole catalog — it
discovers every bundle, imports it, checks its skills and MCP names, and compiles each agent
and workflow to an ``InvocableSpec`` — while opening no connection, starting no MCP server and
making no model call. That is what makes it the right check here: a broken example fails to
build, and the suite stays offline and deterministic.

Do not "fix" this into ``deck.run(...)``. A chat turn needs a real model, and no test in this
suite is allowed to reach one; the docs suite already executes turns against a scripted local
endpoint, and that is where a run-level example check would belong.

One ``Deck`` per test function, deliberately: discovery mounts every project under a single
module alias, so two live decks in one process read each other's bundles.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from test_docs_site import _assert_agentdeck_imports_exist

from agentdeck import Deck

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
DECKS = sorted(p.parent for p in EXAMPLES.glob("*/.agentdeck"))


def test_the_examples_directory_has_not_moved() -> None:
    """Every other test here is parametrized over what the glob found, so an empty glob would
    pass the file silently rather than fail it.
    """
    assert [p.name for p in DECKS] == [
        "agent-with-a-skill",
        "chat-agent-with-a-tool",
        "existing-langgraph-agent",
        "workflow-with-an-approval",
    ]


@pytest.mark.parametrize("example", DECKS, ids=lambda p: p.name)
def test_every_example_deck_builds(example: Path) -> None:
    deck = Deck.from_project(example / ".agentdeck").build()
    assert sorted(deck.agents) or sorted(deck.workflows), f"{example.name} discovered nothing"


def test_the_chat_example_declares_an_agent_holding_its_tool() -> None:
    deck = Deck.from_project(EXAMPLES / "chat-agent-with-a-tool" / ".agentdeck").build()
    assert sorted(deck.agents) == ["OrderDesk"]
    assert [tool.name for tool in deck.agents["OrderDesk"].tools] == ["order_status"]


def test_the_skill_example_declares_an_agent_holding_its_skill() -> None:
    """The skill is this example's whole subject. ``skills=["shift-notes"]`` is a *name*, resolved
    against the discovered bundles at build time, so a renamed directory or a frontmatter ``name``
    that stops matching it fails here rather than at the first model call.
    """
    deck = Deck.from_project(EXAMPLES / "agent-with-a-skill" / ".agentdeck").build()
    assert sorted(deck.agents) == ["HandoverDesk"]
    assert [tool.name for tool in deck.agents["HandoverDesk"].tools] == ["lookup_shift", "file_handover_note"]
    assert deck.agents["HandoverDesk"].skills == ("shift-notes",)
    assert deck.skills is not None, "the example declares a skill, so discovery must have found a root"
    assert sorted(deck.skills.build()) == ["shift-notes"]


def test_the_approval_example_declares_a_durable_workflow() -> None:
    """``durable=True`` is what gives the workflow a checkpointer, and without one ``interrupt()``
    raises instead of parking the run — the example's whole subject.
    """
    deck = Deck.from_project(EXAMPLES / "workflow-with-an-approval" / ".agentdeck").build()
    assert sorted(deck.workflows) == ["RefundApproval"]
    assert deck.workflows["RefundApproval"].durable is True


def test_the_wrapping_example_declares_a_workflow_over_a_graph_it_did_not_write() -> None:
    """The example's whole claim is that the graph module stays agentdeck-free, so assert that
    rather than only that the deck builds: an import added to ``pipeline.py`` would leave every
    other check here green while making the README false.
    """
    example = EXAMPLES / "existing-langgraph-agent"
    deck = Deck.from_project(example / ".agentdeck").build()
    assert sorted(deck.workflows) == ["Triage"]
    assert deck.workflows["Triage"].durable is False

    # Imports, not a substring search: the module's own docstring names AgentDeck to say it does
    # not depend on it, and that sentence is not the dependency the README promises is absent.
    pipeline = ast.parse((example / ".agentdeck/workflows/triage/pipeline.py").read_text())
    imported = {
        name.split(".")[0]
        for node in ast.walk(pipeline)
        for name in (
            [node.module or ""]
            if isinstance(node, ast.ImportFrom)
            else [a.name for a in node.names]
            if isinstance(node, ast.Import)
            else []
        )
    }
    assert "agentdeck" not in imported, f"the example's 'existing' graph imports agentdeck: {sorted(imported)}"


@pytest.mark.parametrize("example", DECKS, ids=lambda p: p.name)
def test_every_example_has_the_run_script_its_readme_tells_you_to_run(example: Path) -> None:
    """``python run.py`` is an instruction, and an instruction naming a file that does not exist
    (or no longer imports what it imports) is the rot this whole issue is about.
    """
    script = example / "run.py"
    assert script.is_file(), f"{example.name}/README.md says `python run.py`, but there is none"
    _assert_agentdeck_imports_exist(script.read_text(), script)
