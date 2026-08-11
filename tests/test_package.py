"""``agentdeck.__version__`` (#176): the one thing a bug reporter is asked for first — and the
distribution metadata around it, which no other test reads.
"""

from __future__ import annotations

import importlib
import tomllib
from functools import cache
from importlib.metadata import PackageNotFoundError, metadata, version
from pathlib import Path

import agentdeck


@cache
def _pyproject() -> dict:
    return tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())


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


def test_the_built_metadata_names_the_license():
    """A `LICENSE` file in the repo is invisible to pip, PyPI and every SBOM scanner — they read
    the distribution metadata, and an unlicensed dependency is a blocker in most companies.

    Reads the installed distribution rather than `pyproject.toml`, so it fails if the field
    stops surviving the build (a backend change, a `license-files` typo) and not only if
    someone deletes the source line.
    """
    assert metadata("agentdeck")["License-Expression"] == "MIT"


def test_python_classifiers_agree_with_requires_python():
    """Classifiers are version claims, and version claims rot — this is the same habit that keeps
    the docs site's install pins honest. Dropping a Python version from `requires-python` without
    dropping its classifier leaves the package advertising support it no longer has.
    """
    project = _pyproject()["project"]
    floor = tuple(int(part) for part in project["requires-python"].removeprefix(">=").strip().split("."))
    claimed = [
        tuple(int(part) for part in classifier.rsplit(" :: ", 1)[1].split("."))
        for classifier in project["classifiers"]
        if classifier.startswith("Programming Language :: Python :: ") and "." in classifier
    ]
    assert claimed, "no Programming Language :: Python :: X.Y classifier — the package claims no Python version"
    assert min(claimed) == floor, f"lowest classified Python {min(claimed)} != requires-python floor {floor}"


def test_no_license_classifier_alongside_the_spdx_expression():
    """Declaring both is a hard build error on the current backend, so a well-meant
    `License :: OSI Approved :: MIT License` breaks `make build` — after the gate has passed.
    """
    assert not [c for c in _pyproject()["project"]["classifiers"] if c.startswith("License ::")]
