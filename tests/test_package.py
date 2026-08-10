"""``agentdeck.__version__`` (#176): the one thing a bug reporter is asked for first."""

from __future__ import annotations

import importlib
from importlib.metadata import PackageNotFoundError, version

import agentdeck


def test_version_matches_the_installed_distribution():
    assert agentdeck.__version__ == version("agentdeck")


def test_version_is_exported():
    assert "__version__" in agentdeck.__all__


def test_version_falls_back_when_not_installed(monkeypatch):
    def _raise(_name: str) -> str:
        raise PackageNotFoundError

    monkeypatch.setattr("importlib.metadata.version", _raise)
    reloaded = importlib.reload(agentdeck)
    try:
        assert reloaded.__version__ == "0+unknown"
    finally:
        importlib.reload(agentdeck)  # restore the real value for any test that runs after
