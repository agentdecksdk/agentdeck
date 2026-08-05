"""Import shim: ``SessionFactory`` relocated to the openai-agents adapter (ADR-D5:
engine-private working memory belongs to its engine).

``agentdeck/app.py`` (v1) still imports it from here, so this path keeps re-exporting
rather than making every v1 caller chase the move. New code imports the real module.
"""

from __future__ import annotations

from agentdeck.adapters.engines.openai_agents.sessions import SessionFactory

__all__ = ["SessionFactory"]
