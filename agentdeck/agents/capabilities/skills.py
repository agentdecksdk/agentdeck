"""Skills capability — local-directory bundle source with allow-list filtering."""

from __future__ import annotations

from pathlib import Path

from agents.sandbox.capabilities import Skills
from agents.sandbox.capabilities.skills import LocalDirLazySkillSource, SkillMetadata
from agents.sandbox.entries import LocalDir
from agents.sandbox.manifest import Manifest
from agents.sandbox.workspace_paths import SandboxPathGrant

from agentdeck.skills import SkillRegistry


class _AllowListedSkillSource(LocalDirLazySkillSource):
    # SDK calls list_skill_metadata both to enumerate and during
    # load_skill, so filtering here also blocks load_skill('not_allowed').
    allowed_skills: frozenset[str] = frozenset()

    def list_skill_metadata(
        self,
        *,
        skills_path: str,
        source_grants: tuple[SandboxPathGrant, ...] = (),
    ) -> list[SkillMetadata]:
        # ``source_grants`` is forwarded so the SDK's ``_resolved_src_input``
        # can match a host root outside the SDK process base_dir against the
        # manifest's ``extra_path_grants`` (v0.17.0+ contract).
        meta = super().list_skill_metadata(skills_path=skills_path, source_grants=source_grants)
        return [m for m in meta if m.name in self.allowed_skills] if self.allowed_skills else meta


class _AutoGrantedSkills(Skills):
    """``Skills`` that auto-grants its lazy source dir as a path grant.

    The agent author has already approved the host directory by passing
    it as ``skills_dir``, so the v0.17.0 ``SandboxPathGrant`` contract
    ("treat ``extra_path_grants`` as trusted application configuration")
    is satisfied. Adding the grant here lets the SDK resolve the source
    root even when the SDK process cwd is outside the skills tree.
    """

    def process_manifest(self, manifest: Manifest) -> Manifest:
        manifest = super().process_manifest(manifest)
        if isinstance(self.lazy_from, _AllowListedSkillSource):
            src = self.lazy_from.source.src
            if src is not None:
                grant = SandboxPathGrant(path=str(src), read_only=True)
                if grant not in manifest.extra_path_grants:
                    manifest.extra_path_grants = (*manifest.extra_path_grants, grant)
        return manifest


def build_skills(names: list[str], skills_dir: Path | None) -> Skills:
    """Build SDK :class:`Skills` from a directory, gated by an allow-list."""
    if skills_dir is None:
        raise ValueError(
            "skills are listed but no skills_dir was provided. "
            "Set CapabilitiesSpec(skills_dir=...) to a directory of skill bundles.",
        )
    src = Path(skills_dir).expanduser().resolve()
    if not src.is_dir():
        raise ValueError(f"skills_dir does not exist or is not a directory: {src}")
    if missing := sorted(set(names) - set(SkillRegistry(src).list())):
        raise ValueError(
            f"Unknown skill(s) {missing} under {src}. Available: {sorted(SkillRegistry(src).list())}.",
        )
    return _AutoGrantedSkills(
        lazy_from=_AllowListedSkillSource(
            source=LocalDir(src=src),
            allowed_skills=frozenset(names),
        )
    )


__all__ = ["build_skills"]
