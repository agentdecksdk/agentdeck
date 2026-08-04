"""Golden fixture agent: one instruction, one tool, so the scripted model can drive a
tool-call turn followed by a text turn.
"""

from agents import function_tool

from agentdeck.agents import BaseAgent


@function_tool
def lookup_slot(day: str) -> str:
    """Return the fixed free slot for a day."""
    return f"{day} 09:00"


class Greeter(BaseAgent):
    instructions = "Greet the user and check one slot."
    tools = [lookup_slot]
