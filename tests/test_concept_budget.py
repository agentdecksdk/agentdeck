"""`concept_budget.py`'s dependency arithmetic, which the gate acts on.

It counted a version bump as a new dependency, so every dependabot PR went red on `slop` asking for
a `## Concept budget` section no author was there to write (#760). The parsing is pure, so this
needs no git repository.
"""

from __future__ import annotations

from concept_budget import new_dependencies

HEADER = "+++ b/pyproject.toml"


def test_a_version_bump_is_not_a_new_dependency() -> None:
    bump = [HEADER, '-    "ruff>=0.16.6",', '+    "ruff>=0.16.7",']
    assert new_dependencies(bump) == []


def test_a_genuinely_new_dependency_still_counts() -> None:
    added = [HEADER, '+    "httpx>=0.28",']
    assert new_dependencies(added) == ['"httpx>=0.28",']


def test_a_bump_beside_an_addition_reports_only_the_addition() -> None:
    mixed = [HEADER, '-    "ruff>=0.16.6",', '+    "ruff>=0.16.7",', '+    "httpx>=0.28",']
    assert new_dependencies(mixed) == ['"httpx>=0.28",']


def test_a_dependency_line_outside_pyproject_is_not_a_dependency() -> None:
    elsewhere = ["+++ b/docs/engineering/dependencies.md", '+    "httpx>=0.28",']
    assert new_dependencies(elsewhere) == []
