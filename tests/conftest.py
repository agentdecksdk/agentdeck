"""Suite-wide pytest hooks that don't belong to any one test module."""

import sys

import pytest

from agentdeck.runtime.settings import reset_settings_cache


@pytest.fixture(autouse=True)
def _model_credentials(monkeypatch):
    """Catalog tests use a deterministic placeholder unless they test missing credentials."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    reset_settings_cache()
    yield


def pytest_terminal_summary(terminalreporter, exitstatus, config):  # noqa: ARG001
    # `-q` (the Makefile's test target) prints nothing for passing tests, so the docs
    # executor's split between run and illustrative fences would otherwise be invisible.
    from test_docs_examples import ILLUSTRATIVE_CASES, RUN_CASES

    terminalreporter.write_line(f"docs examples: {len(RUN_CASES)} run, {len(ILLUSTRATIVE_CASES)} illustrative")


@pytest.fixture(autouse=True)
def _release_the_deck_claim():
    """A Deck holds the process until it is closed, and a sync test has no `await aclose()`.

    The net, not the contract: a test that opens a deck still closes it. Reached through
    ``sys.modules`` so that a test which never imported ``agentdeck.deck``  -  every one under
    ``tests/core/``  -  does not pull the SDK stack in through this hook.
    """
    yield
    deck_module = sys.modules.get("agentdeck.deck")
    if deck_module is not None:
        deck_module.Deck._release()
