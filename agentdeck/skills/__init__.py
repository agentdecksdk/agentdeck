"""``Skills``  -  the capability object a ``Deck`` composes for skill disclosure.

A skill is progressive knowledge disclosure inside an agent's own execution, not a program to
run (``docs/delivery/plan-skills.md``). ``Skills`` owns one or more root directories, scans each
one's direct children for ``<root>/<name>/SKILL.md``  -  never recursively  -  and merges the result
into one name-keyed registry at :meth:`Skills.build`. Constructing it reads nothing from disk;
``build()`` is the one place a bad skill directory is reported.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from agentdeck.core.errors import DOCS_URL
from agentdeck.errors import ConfigError, NotFoundError
from agentdeck.skills.bundle import SKILL_MD_FILENAME, SkillBundle

if TYPE_CHECKING:
    from collections.abc import Sequence

_SKILLS_DOCS = f"{DOCS_URL}/build-your-deck/skills"


class Skills:
    """One or more skill roots, merged into one registry at :meth:`build`.

    ``validate=True`` (the default) enforces what the Agent Skills contract requires and a
    permissive scan would not catch: a ``SKILL.md``'s frontmatter ``name`` must match its
    directory name, and it must declare a non-empty ``description``  -  the text a model reads to
    decide whether to use the skill at all. Both fail ``build()`` loudly instead of registering
    a skill nothing can address or nothing can choose.
    """

    def __init__(self, *roots: str | Path, validate: bool = True) -> None:
        if not roots:
            raise ConfigError("Skills() needs at least one root directory.")
        self._roots = tuple(Path(r).expanduser() for r in roots)
        self._validate = validate
        self._bundles: dict[str, SkillBundle] | None = None

    @property
    def roots(self) -> tuple[Path, ...]:
        return self._roots

    def build(self) -> dict[str, SkillBundle]:
        """Scan every root (direct children only) and merge into one registry.

        A root that does not exist on disk contributes no skills rather than failing  -  a
        project need not have created its skills directory yet. Re-scans on every call; nothing
        here holds a resource worth caching across calls.
        """
        merged: dict[str, SkillBundle] = {}
        found_under: dict[str, Path] = {}
        for root in self._roots:
            resolved = root.resolve()
            if not resolved.is_dir():
                continue
            for child in sorted(resolved.iterdir()):
                if not child.is_dir() or child.name.startswith((".", "_")):
                    continue
                if not (child / SKILL_MD_FILENAME).is_file():
                    continue
                bundle = SkillBundle.from_path(child)
                if self._validate:
                    _validate_bundle(bundle, child)
                if bundle.name in merged:
                    raise ConfigError(
                        f"skill {bundle.name!r} found under both {found_under[bundle.name]} and "
                        f"{child}  -  a skill name must be unique across roots  -  see {_SKILLS_DOCS}"
                    )
                merged[bundle.name] = bundle
                found_under[bundle.name] = child
        self._bundles = merged
        return dict(merged)

    def list(self, *, refresh: bool = False) -> dict[str, SkillBundle]:
        if refresh or self._bundles is None:
            self.build()
        assert self._bundles is not None  # populated by build() on the line above
        return dict(self._bundles)

    def get(self, name: str) -> SkillBundle:
        bundles = self.list()
        try:
            return bundles[name]
        except KeyError:
            raise NotFoundError(
                f"No skill named {name!r} under {[str(r) for r in self._roots]}. Available: {sorted(bundles)}."
            ) from None

    def disclosure_text(self, names: Sequence[str]) -> str:
        """The instructions-block for an agent's own ``skills=[...]``: each name and
        description, never the full body  -  a model still decides whether to use one instead of
        finding it pre-loaded into its context."""
        if not names:
            return ""
        lines = [
            "\n\n### Skills available",
            "Each entry below is a name and a description. Call `load_skill(name)` to read the "
            "full instructions before following one.",
        ]
        lines.extend(f"- {name}: {self.get(name).description}" for name in names)
        return "\n".join(lines)


def _validate_bundle(bundle: SkillBundle, path: Path) -> None:
    if bundle.name != path.name:
        raise ConfigError(
            f"{path / SKILL_MD_FILENAME}: frontmatter declares name {bundle.name!r}, which must "
            f"match its directory name {path.name!r}  -  see {_SKILLS_DOCS}"
        )
    if not bundle.description:
        raise ConfigError(
            f"{path / SKILL_MD_FILENAME}: missing a 'description' in its frontmatter  -  see {_SKILLS_DOCS}"
        )


__all__ = ["Skills", "SkillBundle"]
