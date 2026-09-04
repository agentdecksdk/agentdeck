"""Regenerate-and-diff for `.codex/agents/*.toml` and `.agents/skills/`, both generated from
`.claude/` by `scripts/sync_claude_to_codex.py`. Run `python3 scripts/sync_claude_to_codex.py`
after editing `.claude/agents/` or a synced skill, and this suite fails loudly if that step was
skipped.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sync_claude_to_codex import CLAUDE_ROOT, CODEX_AGENTS_ROOT, CODEX_SKILLS_ROOT, render_codex_agent

if TYPE_CHECKING:
    from pathlib import Path

_REGEN_HINT = "run `python3 scripts/sync_claude_to_codex.py` to regenerate it"


def test_codex_agents_match_the_generator() -> None:
    for source in sorted((CLAUDE_ROOT / "agents").glob("*.md")):
        name, content = render_codex_agent(source)
        destination = CODEX_AGENTS_ROOT / f"{name}.toml"
        assert destination.is_file(), f"{destination} is missing  -  {_REGEN_HINT}"
        assert destination.read_text() == content, f"{destination} is stale  -  {_REGEN_HINT}"


def _tree_files(root: Path) -> dict[Path, bytes]:
    return {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_codex_skills_match_the_source() -> None:
    for source in sorted(CLAUDE_ROOT.glob("skills/*")):
        if not source.is_dir() or not (source / "SKILL.md").is_file():
            continue
        destination = CODEX_SKILLS_ROOT / source.name
        assert destination.is_dir(), f"{destination} is missing  -  {_REGEN_HINT}"
        assert _tree_files(source) == _tree_files(destination), f"{destination} is stale  -  {_REGEN_HINT}"
