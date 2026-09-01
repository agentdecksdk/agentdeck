"""The public import surface (`docs/engineering/architecture.md` 3): one canonical path per
concept, and widening the root is a deliberate diff rather than a side effect.
"""

from __future__ import annotations

import agentdeck
from agentdeck import bindings, errors, mcp, observers, skills, testing

FEATURES = (errors, observers, skills, mcp, bindings, testing)

ROOT = {
    "Agent",
    "AgentInstance",
    "AudioBlock",
    "ContentBlock",
    "DataBlock",
    "Deck",
    "Event",
    "ImageBlock",
    "Observer",
    "ResourceBlock",
    "Run",
    "RunStatus",
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


def test_agentdeck_errors_owns_the_whole_taxonomy() -> None:
    """`AgentdeckError` included: a root alias for it would be the one duplicate path the rule
    forbids, and `except AgentdeckError` reads the same after one import line."""
    assert not [name for name in agentdeck.__all__ if name.endswith("Error")]
    assert {"AgentdeckError", "RunSuspendedError", "RunStateError", "NotFoundError"} <= set(errors.__all__)


def test_no_public_name_lives_in_two_namespaces() -> None:
    """Every feature namespace, not just one: an alias re-exported from `skills` tomorrow is the
    same drift as the content blocks in `bindings` were (#547)."""
    for feature in FEATURES:
        collisions = set(agentdeck.__all__) & set(feature.__all__)
        assert not collisions, f"{feature.__name__} duplicates {sorted(collisions)}"
    for index, feature in enumerate(FEATURES):
        for other in FEATURES[index + 1 :]:
            shared = set(feature.__all__) & set(other.__all__)
            assert not shared, f"{feature.__name__} and {other.__name__} share {sorted(shared)}"
