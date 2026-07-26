import pytest

from agentdeck.agents.web_search import _search
from agentdeck.runtime.settings import reset_settings_cache


@pytest.mark.asyncio
async def test_web_search_without_key_returns_error_line(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    reset_settings_cache()
    try:
        result = await _search("anything")
    finally:
        reset_settings_cache()
    assert result == "error: web_search_unavailable: TAVILY_API_KEY is not configured"
