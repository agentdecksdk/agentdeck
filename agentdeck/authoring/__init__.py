"""The v3 authoring surface: declare agents and workflows, compile to ``InvocableSpec``.

``Deck`` (composition) and the Runtime (execution) are elsewhere — this package only turns
declarations into what an engine runs.
"""

from agentdeck.authoring.agent import Agent, AgentDeclaration
from agentdeck.authoring.interrupts import InterruptResult
from agentdeck.authoring.nodes import AgentNode, LoadFileNode
from agentdeck.authoring.timers import sleep_until
from agentdeck.authoring.workflow import Workflow, WorkflowDeclaration

__all__ = [
    "Agent",
    "AgentDeclaration",
    "AgentNode",
    "InterruptResult",
    "LoadFileNode",
    "Workflow",
    "WorkflowDeclaration",
    "sleep_until",
]
