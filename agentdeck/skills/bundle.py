"""Parse a skill bundle directory and discover bundles under a root path."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from agentdeck.errors import NotFoundError

if TYPE_CHECKING:
    from collections.abc import Iterable

    from agentdeck.skills.output import SkillOutputSchema

DEFAULT_ENTRY_SCRIPT = "run.py"
SCRIPTS_DIRNAME = "scripts"
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

    @property
    def scripts_dir(self) -> Path:
        return self.path / SCRIPTS_DIRNAME

    def entry_script_path(self, name: str = DEFAULT_ENTRY_SCRIPT) -> Path:
        return self.scripts_dir / name

    @property
    def required_env_keys(self) -> tuple[str, ...]:
        return _env_keys(self.frontmatter, "required")

    @property
    def optional_env_keys(self) -> tuple[str, ...]:
        return _env_keys(self.frontmatter, "optional")

    @property
    def output_schema(self) -> type[SkillOutputSchema] | None:
        """Typed output class from ``metadata.output_schema`` — workflow-only."""
        if (decl := self._schema_decl()) is None:
            return None
        from agentdeck.skills.output import load_schema

        return load_schema(self.path / decl[0], decl[1])

    def _schema_decl(self) -> tuple[str, str] | None:
        metadata = self.frontmatter.get("metadata") or {}
        spec = metadata.get("output_schema") if isinstance(metadata, dict) else None
        if not spec:
            return None
        rel, sep, cls_name = str(spec).partition(":")
        if not sep or not rel.strip() or not cls_name.strip():
            raise ValueError(
                f"{self.name}: metadata.output_schema must be 'relative/path.py:ClassName', got {spec!r}.",
            )
        return rel.strip(), cls_name.strip()


@dataclass(slots=True)
class SkillRegistry:
    """Auto-discovers :class:`SkillBundle` directories under ``root``."""

    root: Path
    _cache: dict[str, SkillBundle] | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.root = Path(self.root).expanduser().resolve()

    def list(self, *, refresh: bool = False) -> dict[str, SkillBundle]:
        if refresh or self._cache is None:
            self._cache = {b.name: b for b in self._scan()}
        return dict(self._cache)

    def get(self, name: str) -> SkillBundle:
        skills = self.list()
        try:
            return skills[name]
        except KeyError:
            raise NotFoundError(
                f"No skill named {name!r} under {self.root}. Available: {sorted(skills)}.",
            ) from None

    def _scan(self) -> Iterable[SkillBundle]:
        if not self.root.is_dir():
            return
        for child in sorted(self.root.iterdir()):
            if child.is_dir() and not child.name.startswith(("_", ".")) and (child / SKILL_MD_FILENAME).is_file():
                yield SkillBundle.from_path(child)


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
        raise ValueError(f"invalid YAML frontmatter: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"frontmatter must be a YAML mapping, got {type(data).__name__}")
    return data, text[match.end() :]


def _env_keys(frontmatter: dict[str, Any], kind: str) -> tuple[str, ...]:
    metadata = frontmatter.get("metadata") or {}
    env = metadata.get("env") if isinstance(metadata, dict) else None
    section = env.get(kind) if isinstance(env, dict) else None
    if isinstance(section, dict | list):
        return tuple(str(k) for k in section)
    return ()


__all__ = ["SkillBundle", "SkillRegistry"]
