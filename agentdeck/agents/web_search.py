"""Tavily-backed ``web_search`` function tool.

Model-agnostic web search (works with Gemini / any OpenAI-compatible model,
unlike the SDK's hosted ``WebSearchTool``). One knob: ``TAVILY_API_KEY``
env var or ``tavily: api_key:`` in config.yaml. Without a key the tool
returns an ``error:`` line instead of raising, so the agent degrades the
same way an unavailable MCP server does.
"""

from __future__ import annotations

import httpx
from agents import function_tool

from agentdeck.runtime.settings import get_settings

_TAVILY_URL = "https://api.tavily.com/search"


async def _search(query: str) -> str:
    api_key = get_settings().tavily.api_key
    if not api_key:
        return "error: web_search_unavailable: TAVILY_API_KEY is not configured"
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            _TAVILY_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"query": query, "max_results": 5},
        )
        response.raise_for_status()
    results = response.json().get("results", [])
    if not results:
        return "No results."
    return "\n\n".join(f"{r.get('title', '')}\n{r.get('url', '')}\n{r.get('content', '')}" for r in results)


@function_tool(name_override="web_search")
async def web_search(query: str) -> str:
    """Search the web; returns top results as title / URL / snippet blocks."""
    return await _search(query)


__all__ = ["web_search"]
