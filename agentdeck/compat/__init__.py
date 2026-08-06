"""v1 compatibility: what lets v1's public surface run on the v2 Runtime, and nothing else.

Deliberately not under `adapters/`: this code reaches into v1's runner glue and v1's Langfuse
integration on purpose, which an engine adapter may never do (the import law holds v2 code to
"one external system each", and only the telemetry adapter may touch Langfuse). Keeping the
bridge on the v1 side of that line is what keeps the adapter clean. The whole directory goes
away with v1's runner glue in the pre-stable cleanup.
"""

from agentdeck.compat.engine import STRUCTURED_OUTPUT, V1CompatEngine

__all__ = ["STRUCTURED_OUTPUT", "V1CompatEngine"]
