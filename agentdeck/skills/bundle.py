"""Parse one ``SKILL.md`` bundle directory."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from agentdeck.errors import ConfigError

SKILL_MD_FILENAME = "SKILL.md"

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass(slots=True)
class SkillBundle:
    """A parsed skill bundle on disk. Construct via :meth:`from_path`."""

    name: str
    path: Path
    description: str = ""
    frontmatter: dict[str, Any] = field(default_factory=dict)
    body: str = ""

    @classmethod
    def from_path(cls, path: str | Path, *, name: str | None = None) -> SkillBundle:
        bundle_path = Path(path).expanduser().resolve()
        if not bundle_path.is_dir():
            raise FileNotFoundError(f"skill bundle not found: {bundle_path}")
        skill_md = bundle_path / SKILL_MD_FILENAME
        if not skill_md.is_file():
            raise FileNotFoundError(f"missing {SKILL_MD_FILENAME} under {bundle_path}")
        frontmatter, body = _split_frontmatter(skill_md.read_text(encoding="utf-8"))
        return cls(
            name=name or _str_field(frontmatter, "name") or bundle_path.name,
            path=bundle_path,
            description=_str_field(frontmatter, "description").strip(),
            frontmatter=frontmatter,
            body=body,
        )


def _str_field(frontmatter: dict[str, Any], key: str) -> str:
    value = frontmatter.get(key)
    return value if isinstance(value, str) else ""


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    try:
        data = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML frontmatter: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"frontmatter must be a YAML mapping, got {type(data).__name__}")
    return data, text[match.end() :]


__all__ = ["SKILL_MD_FILENAME", "SkillBundle"]
