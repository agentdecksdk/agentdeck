"""Sandbox-aware LangGraph workflows.

Build the graph with LangGraph's :class:`StateGraph`; drop in
:class:`SkillNode`, :class:`AgentNode`, or :class:`SandboxAgentNode`
to give skills or agents a turn — they share the workflow's
:class:`Workspace`. :class:`LoadFileNode` pulls a sandbox file back
into state.
"""

from langgraph.graph import END, StateGraph

from agentdeck.workflows.base import BaseWorkflow
from agentdeck.workflows.nodes import AgentNode, LoadFileNode, SandboxAgentNode, SkillExecutionError, SkillNode
from agentdeck.workflows.registry import WorkflowRegistry
from agentdeck.workflows.runners import BaseWorkflowRunner, DevWorkflowRunner
from agentdeck.workflows.state import coerce_input, dump_state, json_default

__all__ = [
    "END",
    "AgentNode",
    "BaseWorkflow",
    "BaseWorkflowRunner",
    "DevWorkflowRunner",
    "LoadFileNode",
    "SandboxAgentNode",
    "SkillExecutionError",
    "SkillNode",
    "StateGraph",
    "WorkflowRegistry",
    "coerce_input",
    "dump_state",
    "json_default",
]
