"""Anti-rot checks for the copyable decks under ``examples/``.

Each example is **built, never run**. ``Deck.build()`` validates the whole catalog  -  it
discovers every bundle, imports it, checks its skills and MCP names, and compiles each agent
and workflow to an ``InvocableSpec``  -  while opening no connection, starting no MCP server and
making no model call. That is what makes it the right check here: a broken example fails to
build, and the suite stays offline and deterministic.

Do not "fix" this into ``deck.run(...)``. A chat turn needs a real model, and no test in this
suite is allowed to reach one; the docs suite already executes turns against a scripted local
endpoint, and that is where a run-level example check would belong.

One example is exempt because it reaches no model either: ``run-events-stream`` stubs the SDK
boundary in its own script, so running it keeps the suite offline and deterministic, which is
what the rule above protects rather than "never execute an example".

One ``Deck`` per test function, deliberately: discovery mounts every project under a single
module alias, so two live decks in one process read each other's bundles.
"""

from __future__ import annotations

import re
import subprocess
import sys
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
        "chat-in-the-terminal",
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


@pytest.mark.parametrize("example", DECKS, ids=lambda p: p.name)
def test_every_example_has_the_run_script_its_readme_tells_you_to_run(example: Path) -> None:
    """``python run.py`` is an instruction, and an instruction naming a file that does not exist
    (or no longer imports what it imports) is the rot this whole issue is about. Read from the
    README rather than assumed: an example run by `agentdeck chat` has no script to name.
    """
    readme = (example / "README.md").read_text()
    if "python run.py" not in readme:
        return
    script = example / "run.py"
    assert script.is_file(), f"{example.name}/README.md says `python run.py`, but there is none"
    _assert_agentdeck_imports_exist(script.read_text(), script)


@pytest.mark.parametrize("example", DECKS, ids=lambda p: p.name)
def test_every_agentdeck_chat_instruction_names_a_real_target(example: Path) -> None:
    """The same rot in the other shape: `agentdeck chat <target>` naming something the deck does
    not discover fails at the reader's first command.
    """
    named = re.findall(r"^agentdeck chat (\S+)$", (example / "README.md").read_text(), re.MULTILINE)
    if not named:
        return
    deck = Deck.from_project(example / ".agentdeck").build()
    targets = set(deck.agents) | set(deck.workflows)
    for target in named:
        assert target in targets, f"{example.name}/README.md says `agentdeck chat {target}`: {sorted(targets)}"


def test_the_terminal_example_declares_a_workflow_and_needs_no_model() -> None:
    """The example's whole point is that it runs with no credentials: a `@workflow` is the
    caller's own Python, so a deck holding only one reaches no model at all.
    """
    deck = Deck.from_project(EXAMPLES / "chat-in-the-terminal" / ".agentdeck").build()
    assert sorted(deck.workflows) == ["shift_handover"]
    assert not deck.agents


def test_the_events_example_prints_the_lifecycle_in_order() -> None:
    """The README's output block is the example's whole payload, so the sequence behind it is what
    rots. Kinds only: pinning the payload text here would fail on every ``ScriptedModel`` default
    that changes, which is not this example's contract.
    """
    result = subprocess.run(
        [sys.executable, "run.py"],
        cwd=EXAMPLES / "run-events-stream",
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    kinds = [line.split(":", 1)[0] for line in result.stdout.splitlines()]
    assert kinds == [
        "run.started",
        "text.delta",
        "text.delta",
        "usage.reported",
        "message.completed",
        "run.completed",
    ]
