"""Golden-suite fixtures: the real FastAPI app over the committed fixture project, with
the model provider swapped for a fixed-constant :class:`ScriptedModel` and every env knob
pinned.
"""

import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

from agentdeck.testing import ScriptedModel, patch_model

pytest.importorskip("fastapi")

FIXTURE_PROJECT = Path(__file__).parent / "fixture_project"

# Turn 1 calls the fixture agent's tool, turn 2 (and any later turn) answers in text: see
# tests/golden/README.md's "Normalization rules" for why every value here is a constant.
ANSWER_DELTAS = ("Tuesday ", "at 9am ", "works.")
TOOL_NAME = "lookup_slot"

# Env that must not leak in from a developer's shell or a stray .env: Redis sessions and
# Langfuse export would both reach outside the test, and max_turns below the scripted two
# would truncate the recorded turn.
_PINNED_ENV = {
    "AGENTDECK_EVENTS": "memory://",
    "AGENTDECK_SESSION": "",
    "AGENTDECK_LANGFUSE_PUBLIC_KEY": "",
    "AGENTDECK_LANGFUSE_SECRET_KEY": "",
    "AGENTDECK_RUNNER_MAX_TURNS": "30",
    "OPENAI_API_KEY": "golden",
    "OPENAI_BASE_URL": "",
    "OPENAI_MODEL": "fake-golden",
}


def _golden_model() -> ScriptedModel:
    """A fresh model per provider construction: one per request, so every capture's own
    turn count starts back at the tool call rather than carrying over from the last one."""
    return ScriptedModel(deltas=ANSWER_DELTAS, tool_name=TOOL_NAME, input_tokens=11, output_tokens=5)


@pytest.fixture
def make_client(monkeypatch):
    """Factory of independent clients: the stability test needs two fresh ones in a row."""
    from fastapi.testclient import TestClient

    from agentdeck.runtime.settings import reset_settings_cache
    from agentdeck.serve import create_app

    for key, value in _PINNED_ENV.items():
        monkeypatch.setenv(key, value)
    # .env resolves from cwd at settings-build time, and chdir below puts that at the fixture.
    monkeypatch.chdir(FIXTURE_PROJECT)

    with patch_model(_golden_model):

        @contextmanager
        def _make():
            reset_settings_cache()
            for mod in [m for m in sys.modules if m.startswith("agentdeck_project")]:
                del sys.modules[mod]
            # A plain (non-AgentdeckError) failure is answered by ServerErrorMiddleware, which
            # re-raises after sending its response so a real server can still log it: the
            # default client would surface that as a raised exception instead of a response.
            # This suite records wire bytes, exactly what a real client sees, never that.
            with TestClient(create_app(), raise_server_exceptions=False) as client:
                yield client

        yield _make
        reset_settings_cache()
