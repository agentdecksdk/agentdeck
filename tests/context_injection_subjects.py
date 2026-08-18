"""Callables annotated **eagerly**  -  this module deliberately omits ``from __future__ import
annotations``, which every other module here has.

The pair matters: ``tests/test_context_injection.py`` uses postponed annotations, so its own
subjects carry source strings and this module's carry real objects. Analyzing both and asserting
the same result is what shows the analysis reads resolved hints rather than whatever
``__annotations__`` happens to hold.
"""

from agentdeck.core.context import Context


class Calendar:
    """An application object, the sort a run is handed rather than told about."""


def find_slots(date: str, environment: Context[Calendar]) -> str:
    return f"{date}:{environment.data}"
