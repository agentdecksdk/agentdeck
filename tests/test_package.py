"""``agentdeck.__version__`` (#176): the one thing a bug reporter is asked for first  -  and the
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


def _installed_dir() -> Path:
    """Where the package actually resolves from  -  ``__path__``, not the repo tree, so a file
    that never made it through the build cannot pass a check about the built package."""
    return Path(next(iter(agentdeck.__path__)))


def test_version_matches_the_installed_distribution():
    assert agentdeck.__version__ == version("agentdeck-sdk")


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
    """A `LICENSE` file in the repo is invisible to pip, PyPI and every SBOM scanner  -  they read
    the distribution metadata, and an unlicensed dependency is a blocker in most companies.

    Reads the installed distribution rather than `pyproject.toml`, so it fails if the field
    stops surviving the build (a backend change, a `license-files` typo) and not only if
    someone deletes the source line.
    """
    assert metadata("agentdeck-sdk")["License-Expression"] == "MIT"


def test_the_installed_package_ships_its_typing_marker():
    """Without `py.typed`, PEP 561 says a consumer's type checker must ignore our annotations
    entirely  -  75 fully annotated modules and a `ty` gate buy a downstream user nothing.

    Reads where the package actually resolves from rather than a hardcoded repo path. Under an
    editable install that is the source tree, so this catches a deleted or never-tracked marker,
    not a packaging exclusion  -  `[tool.hatch.build.targets.wheel] packages = ["agentdeck"]`
    carries package data, verified against a built wheel when this landed.
    """
    assert (_installed_dir() / "py.typed").is_file(), "py.typed did not survive into the installed package"


def test_the_typed_classifier_matches_the_marker():
    """The classifier is a claim; `py.typed` is what makes it true. Neither may outlive the
    other  -  a `Typing :: Typed` claim with no marker is a lie to anyone reading PyPI."""
    claims_typed = "Typing :: Typed" in _pyproject()["project"]["classifiers"]
    assert claims_typed == (_installed_dir() / "py.typed").is_file()


def test_python_classifiers_agree_with_requires_python():
    """Classifiers are version claims, and version claims rot  -  this is the same habit that keeps
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
    assert claimed, "no Programming Language :: Python :: X.Y classifier  -  the package claims no Python version"
    assert min(claimed) == floor, f"lowest classified Python {min(claimed)} != requires-python floor {floor}"


def test_every_console_script_points_at_something_importable():
    """`agentdeck-serve` and `agentdeck` are commands the README and the docs tell people to type.
    A renamed or deleted target only shows up when someone types one, because installing the
    package writes the wrapper either way.
    """
    for command, target in _pyproject()["project"]["scripts"].items():
        module_name, _, function = target.partition(":")
        module = importlib.import_module(module_name)
        assert callable(getattr(module, function, None)), f"`{command}` points at a missing {target}"


def test_no_license_classifier_alongside_the_spdx_expression():
    """Declaring both is a hard build error on the current backend, so a well-meant
    `License :: OSI Approved :: MIT License` breaks `make build`  -  after the gate has passed.
    """
    assert not [c for c in _pyproject()["project"]["classifiers"] if c.startswith("License ::")]


def test_redis_is_an_extra_not_a_base_dependency():
    """#253/#274: nothing defaults to redis (`AGENTDECK_SESSION`/`AGENTDECK_EVENTS` both default
    off it), so the client moves out of base into its own `[redis]` extra  -  kept in `[dev]` too
    so `make check` still exercises the redis-backed paths."""
    project = _pyproject()["project"]
    assert not any(dep.startswith("redis") for dep in project["dependencies"])

    extras = project["optional-dependencies"]
    assert any(dep.startswith("redis") for dep in extras["redis"])
    assert any(dep.startswith("redis") for dep in extras["dev"])
