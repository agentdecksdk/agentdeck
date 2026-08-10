"""Golden fixture agent: one instruction, one tool, so the scripted model can drive a
tool-call turn followed by a text turn.
"""

from agents import function_tool

from agentdeck.authoring import Agent


@function_tool
def lookup_slot(day: str) -> str:
    """Return the fixed free slot for a day."""
    return f"{day} 09:00"


greeter = Agent(name="Greeter", instructions="Greet the user and check one slot.", tools=[lookup_slot])
