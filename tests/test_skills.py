"""``Skills``: direct-child-only discovery, frontmatter validation, multi-root merge — and the
``authoring.skills`` wiring that turns a registry into ``compile_agent``'s ``resolve_skills``
hook (disclosure text + a ``load_skill`` tool scoped to one agent's own allow-list).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from agentdeck.authoring.skills import _load_skill, skills_resolver
from agentdeck.errors import ConfigError, NotFoundError
from agentdeck.skills import Skills

if TYPE_CHECKING:
    from pathlib import Path


def _write_skill(
    root: Path, dirname: str, *, name: str | None = None, description: str | None = "does a thing"
) -> None:
    skill_dir = root / dirname
    skill_dir.mkdir(parents=True)
    frontmatter_lines = []
    if name is not None:
        frontmatter_lines.append(f"name: {name}")
    if description is not None:
        frontmatter_lines.append(f"description: {description}")
    lines = ["---", *frontmatter_lines, "---", f"Body for {dirname}.", ""]
    (skill_dir / "SKILL.md").write_text("\n".join(lines))


def test_direct_child_scan_only_a_nested_skill_is_invisible(tmp_path):
    _write_skill(tmp_path, "booking", name="booking")
    nested = tmp_path / "booking" / "nested"
    _write_skill(tmp_path / "booking", "nested", name="nested")

    bundles = Skills(tmp_path).list()

    assert set(bundles) == {"booking"}
    assert nested.is_dir()  # the nested SKILL.md exists on disk; scan still ignored it


def test_multi_root_merge_into_one_registry(tmp_path):
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    _write_skill(root_a, "booking", name="booking")
    _write_skill(root_b, "rescheduling", name="rescheduling")

    bundles = Skills(root_a, root_b).list()

    assert set(bundles) == {"booking", "rescheduling"}


def test_frontmatter_name_mismatch_fails_build(tmp_path):
    _write_skill(tmp_path, "booking", name="reservation")

    with pytest.raises(ConfigError, match="reservation.*booking"):
        Skills(tmp_path).build()


def test_missing_description_fails_build(tmp_path):
    _write_skill(tmp_path, "booking", name="booking", description=None)

    with pytest.raises(ConfigError, match="description"):
        Skills(tmp_path).build()


def test_validate_false_is_lenient_about_name_and_description(tmp_path):
    _write_skill(tmp_path, "booking", name="reservation", description=None)

    bundles = Skills(tmp_path, validate=False).build()

    # The SDK's own permissive fallback: falls back to the directory name for addressing,
    # keeps whatever description (or lack of one) the frontmatter declared.
    assert set(bundles) == {"reservation"}


def test_duplicate_name_across_roots_names_both_paths(tmp_path):
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    _write_skill(root_a, "booking", name="booking")
    _write_skill(root_b, "booking", name="booking")

    with pytest.raises(ConfigError) as excinfo:
        Skills(root_a, root_b).build()
    message = str(excinfo.value)
    assert str((root_a / "booking").resolve()) in message
    assert str((root_b / "booking").resolve()) in message


def test_unknown_name_raises_not_found_naming_the_roots(tmp_path):
    _write_skill(tmp_path, "booking", name="booking")

    with pytest.raises(NotFoundError, match="booking"):
        Skills(tmp_path).get("does-not-exist")


def test_skills_requires_at_least_one_root():
    with pytest.raises(ConfigError):
        Skills()


def test_a_root_that_does_not_exist_yet_contributes_no_skills(tmp_path):
    assert Skills(tmp_path / "not-created-yet").list() == {}


def test_disclosure_text_lists_name_and_description_never_the_body(tmp_path):
    _write_skill(tmp_path, "booking", name="booking", description="Books appointments.")
    skills = Skills(tmp_path)
    skills.build()

    text = skills.disclosure_text(["booking"])

    assert "booking" in text
    assert "Books appointments." in text
    assert "Body for booking." not in text


def test_disclosure_text_is_empty_for_no_names(tmp_path):
    assert Skills(tmp_path).disclosure_text([]) == ""


def test_skills_resolver_produces_disclosure_and_one_load_skill_tool(tmp_path):
    _write_skill(tmp_path, "booking", name="booking", description="Books appointments.")
    skills = Skills(tmp_path)
    skills.build()
    resolve = skills_resolver(skills)

    disclosure, tools = resolve(["booking"])

    assert "Books appointments." in disclosure
    assert [t.name for t in tools] == ["load_skill"]


def test_load_skill_returns_full_body_for_an_allowed_name(tmp_path):
    _write_skill(tmp_path, "booking", name="booking", description="Books appointments.")
    skills = Skills(tmp_path)
    skills.build()

    result = _load_skill(skills, frozenset({"booking"}), "booking")

    assert "Body for booking." in result


def test_load_skill_refuses_a_name_outside_the_agents_own_allow_list(tmp_path):
    _write_skill(tmp_path, "booking", name="booking", description="Books appointments.")
    _write_skill(tmp_path, "refunds", name="refunds", description="Processes refunds.")
    skills = Skills(tmp_path)
    skills.build()

    # The registry knows "refunds"; this agent's own skills=[...] does not.
    result = _load_skill(skills, frozenset({"booking"}), "refunds")

    assert result.startswith("error: skill_not_available:")
    assert "Body for refunds." not in result
