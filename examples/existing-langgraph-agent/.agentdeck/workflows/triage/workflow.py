"""The whole adoption: four lines naming a graph somebody else already wrote.

The import is relative. A bundle is imported under agentdeck's own module alias, so a sibling
inside the bundle directory is reached as `.pipeline`  -  `import pipeline` fails, and a module
outside `.agentdeck/` is not importable from here at all. Keeping the existing graph beside its
declaration is the supported shape; nothing in it had to change to sit there.
"""

from agentdeck import Workflow

from .pipeline import TicketState, build

triage = Workflow(
    name="Triage",
    description="Classify an inbound ticket and route it.",
    state=TicketState,
    graph=build,
)
