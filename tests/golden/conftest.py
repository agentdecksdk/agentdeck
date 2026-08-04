"""Golden-suite fixtures: the real FastAPI app over the committed fixture project, with
the model provider swapped for :class:`ScriptedProvider` and every env knob pinned.
"""

import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

FIXTURE_PROJECT = Path(__file__).parent / "fixture_project"
SNAPSHOTS = Path(__file__).parent / "snapshots"

# Env that must not leak in from a developer's shell or the repo-root .env: Redis
# sessions, Langfuse export and the sqlite checkpointer would all reach outside the test,
# and max_turns below the scripted two would truncate the recorded turn.
_PINNED_ENV = {
    "AGENTDECK_CHECKPOINT_BACKEND": "memory",
    "AGENTDECK_CHECKPOINT_URL": "",
    "AGENTDECK_SESSION_REDIS_URL": "",
    "AGENTDECK_LANGFUSE_PUBLIC_KEY": "",
    "AGENTDECK_LANGFUSE_SECRET_KEY": "",
    "AGENTDECK_MCP_SERVERS": "{}",
    "AGENTDECK_RUNNER_MAX_TURNS": "30",
    "OPENAI_API_KEY": "golden",
    "OPENAI_BASE_URL": "",
    "OPENAI_MODEL": "fake-golden",
}


@pytest.fixture
def make_client(monkeypatch):
    """Factory of independent clients — the stability test needs two fresh ones in a row."""
    from fake_model import ScriptedProvider
    from fastapi.testclient import TestClient

    from agentdeck.runtime.checkpointer import _memory_saver
    from agentdeck.runtime.settings import PACKAGED_DEFAULT_YAML, reset_settings_cache
    from agentdeck.serve import create_app

    for key, value in _PINNED_ENV.items():
        monkeypatch.setenv(key, value)
    # .env and config.yaml resolve from the *package's* repo root, not the cwd, so chdir
    # alone can't neutralize them — pin the YAML source to the shipped defaults.
    monkeypatch.setenv("APP_CONFIG_PATH", str(PACKAGED_DEFAULT_YAML))
    monkeypatch.setattr("agentdeck.agents.runners.base.OpenAIProvider", ScriptedProvider)
    monkeypatch.chdir(FIXTURE_PROJECT)

    @contextmanager
    def _make():
        # The memory saver is a process-wide @cache; a stale one would carry a previous
        # capture's paused threads into this one's /pending body.
        _memory_saver.cache_clear()
        reset_settings_cache()
        for mod in [m for m in sys.modules if m.startswith("agentdeck_project")]:
            del sys.modules[mod]
        with TestClient(create_app()) as client:
            yield client

    yield _make
    reset_settings_cache()
