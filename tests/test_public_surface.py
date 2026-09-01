"""The public import surface (`docs/engineering/architecture.md` 3): one canonical path per
concept, and widening the root is a deliberate diff rather than a side effect.
"""

from __future__ import annotations

import agentdeck
from agentdeck import bindings, errors

ROOT = {
    "Agent",
    "AgentInstance",
    "AgentdeckError",
    "AudioBlock",
    "ContentBlock",
    "DataBlock",
    "Deck",
    "ImageBlock",
    "Observer",
    "ResourceBlock",
    "Run",
    "TextBlock",
    "ToolCtx",
    "TurnResult",
    "WorkflowCtx",
    "__version__",
    "tool",
    "views",
    "workflow",
}


def test_the_root_exports_the_everyday_vocabulary_and_nothing_else() -> None:
    assert set(agentdeck.__all__) == ROOT


def test_agentdeck_errors_owns_the_taxonomy() -> None:
    """The root carries `AgentdeckError` alone: a subset of the taxonomy at two paths is the
    "where do I import this exception from?" friction this rule exists to remove."""
    at_root = {name for name in agentdeck.__all__ if name.endswith("Error")}
    assert at_root == {"AgentdeckError"}
    assert {"RunSuspendedError", "RunStateError", "NotFoundError"} <= set(errors.__all__)


def test_no_public_name_lives_in_two_namespaces() -> None:
    """An alias is a second path to keep true. The content blocks live at the root, so the SPI
    does not re-export them (#547 did, briefly)."""
    assert not set(agentdeck.__all__) & set(bindings.__all__)
