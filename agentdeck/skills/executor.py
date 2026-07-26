"""Deterministic skill execution: graph-driven counterpart to ``Skills`` cap."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from agents.sandbox.entries import BaseEntry, LocalDir
from agents.sandbox.workspace_paths import SandboxPathGrant

from agentdeck.runtime.observability import RunTrace, init_observability, trace_run
from agentdeck.runtime.workspace import Workspace, current_capture, input_file_entries
from agentdeck.skills.bundle import DEFAULT_ENTRY_SCRIPT, SkillBundle

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

logger = logging.getLogger(__name__)

# Mount path matches the SDK's Skills layout — bundles behave the same
# whether the LLM picks them through the SDK or the graph picks them here.
_SKILLS_MOUNT_DIR = ".agents"
_PYTHON = "python3"
_OUTPUT_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)$")

# NOTE: the shared skill runtime (LLM path, batch/bisect, capture, tracing) is the
# top-level ``skill_runtime`` package installed in the venv — the sandbox shares
# that venv, so skills ``import skill_runtime`` with no mount, exactly as they
# ``import agentdecks_core``. See ``agentdeck/skills/skill_runtime``.


class SkillEnvError(RuntimeError):
    """Raised when a required env var declared in ``SKILL.md`` is missing."""

    def __init__(self, skill: str, missing: Sequence[str]) -> None:
        self.skill = skill
        self.missing = tuple(missing)
        super().__init__(
            f"skill {skill!r} requires env var(s) {sorted(self.missing)} but they are unset.",
        )


@dataclass(frozen=True, slots=True)
class SkillOutput:
    key: str
    value: str


@dataclass(frozen=True, slots=True)
class SkillResult:
    skill: str
    exit_code: int
    stdout: str
    stderr: str
    outputs: tuple[SkillOutput, ...] = ()

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def output(self, key: str) -> str | None:
        # Skills sometimes emit progress lines for the same key while
        # running and a final value at the end; keep the last occurrence.
        for entry in reversed(self.outputs):
            if entry.key == key:
                return entry.value
        return None

    def output_path(self, key: str) -> Path | None:
        value = self.output(key)
        return Path(value) if value is not None else None

    def raise_if_failed(self, stage: str) -> None:
        """Raise ``RuntimeError`` describing a non-zero exit, with clipped stderr."""
        if self.ok:
            return
        raise RuntimeError(
            f"{stage} failed (exit {self.exit_code}): {self.stderr.strip()[:500]}",
        )

    def require_output(self, key: str, stage: str) -> str:
        """Return ``outputs[key]`` or raise ``RuntimeError`` naming the stage."""
        value = self.output(key)
        if value is None:
            raise RuntimeError(f"{stage}: skill did not emit {key}.")
        return value


@dataclass(slots=True)
class SkillExecutor:
    """Run a skill bundle deterministically inside a sandbox session."""

    bundle: SkillBundle
    env: Mapping[str, str] = field(default_factory=dict)
    input_files: Sequence[str | Path] = ()

    async def run(
        self,
        *args: str | Path,
        timeout: float | None = None,
        env_extras: Mapping[str, str] | None = None,
    ) -> SkillResult:
        """Run the skill. ``env_extras`` layers per-call env on top of ``self.env``
        — saves callers from rebuilding the executor for one extra variable
        (e.g. ``SRD_INTERACTIVE=1``).
        """
        # Bootstrap tracing so a skill run — even a standalone one, outside any agent or
        # workflow — opens a host span named after the skill (its subprocess LLM calls
        # nest under it) instead of the subprocess exporting an orphan ``ChatCompletion``
        # root. Idempotent + a no-op without Langfuse keys; already done inside a run.
        init_observability()

        logger.debug("skill %s: start args=%s", self.bundle.name, [str(a) for a in args])
        env = dict(self.env)
        if env_extras:
            env.update(env_extras)
        missing = [k for k in self.bundle.required_env_keys if not env.get(k)]
        if missing:
            raise SkillEnvError(self.bundle.name, missing)

        mount = f"{_SKILLS_MOUNT_DIR}/{self.bundle.name}"
        bundle_root = Path(self.bundle.path).resolve()
        entries: list[tuple[Path, BaseEntry]] = [
            (Path(mount), LocalDir(src=bundle_root)),
            *input_file_entries(self.input_files),
        ]
        # Skill bundles live outside the SDK process cwd in most deploys
        # (the catalog ships under the package install root). v0.17.0+
        # requires explicit grants for ``LocalDir.src`` outside base_dir;
        # the bundle path is part of the application config so it counts
        # as trusted by definition.
        bundle_grant = SandboxPathGrant(path=str(bundle_root), read_only=True)

        # One named span per skill run. Opened BEFORE the sandbox so the skill's own
        # ``TRACEPARENT`` (captured in ``Workspace.open``) points here — the sandboxed
        # LLM calls then nest under this skill span instead of floating up to the run
        # root, and the trace shows *which* skill did the work. Passing the ambient capture
        # re-affirms the session (idempotent under a run; it also tags a *standalone* skill
        # run's trace, which would otherwise be session-less).
        with trace_run(current_capture(), name=self.bundle.name, kind="tool", input=[str(a) for a in args]) as step:
            async with Workspace.open(
                environment=env,
                extra_path_grants=(bundle_grant,),
            ) as ws:
                await ws.materialize(entries)
                script = f"{mount}/{self.bundle.scripts_dir.name}/{DEFAULT_ENTRY_SCRIPT}"
                result = await ws.exec(
                    _PYTHON,
                    script,
                    *(str(a) for a in args),
                    timeout=timeout,
                    shell=False,
                )
            stdout, stderr = _decode(result.stdout), _decode(result.stderr)
            skill_result = SkillResult(
                skill=self.bundle.name,
                exit_code=result.exit_code,
                stdout=stdout,
                stderr=stderr,
                outputs=tuple(parse_skill_outputs(stdout)),
            )
            _record_step(step, skill_result)
            logger.debug(
                "skill %s: exit=%s outputs=%d", self.bundle.name, skill_result.exit_code, len(skill_result.outputs)
            )
            return skill_result

    async def run_checked(
        self,
        *args: str | Path,
        stage: str = "",
        timeout: float | None = None,
        env_extras: Mapping[str, str] | None = None,
    ) -> SkillResult:
        """Run and raise on a non-zero exit. ``stage`` defaults to the bundle name."""
        result = await self.run(*args, timeout=timeout, env_extras=env_extras)
        result.raise_if_failed(stage or self.bundle.name)
        return result

    async def run_output(
        self,
        *args: str | Path,
        output: str,
        stage: str = "",
        timeout: float | None = None,
        env_extras: Mapping[str, str] | None = None,
    ) -> str:
        """Run, raise on failure, then read the artefact named by the ``output``
        key (a ``key=<path>`` stdout line) and return its text.

        The path may be sandbox-relative (the common case) or host-absolute —
        both are read transparently.
        """
        stage = stage or self.bundle.name
        result = await self.run_checked(*args, stage=stage, timeout=timeout, env_extras=env_extras)
        return await _read_artifact(result.require_output(output, stage))


def _record_step(step: RunTrace, result: SkillResult) -> None:
    """Report the skill outcome onto its span: the ``key=value`` contract as output, and
    a clipped ``exit N: <stderr>`` error (which marks the span failed) on a non-zero exit."""
    outputs = {o.key: o.value for o in result.outputs}
    if result.ok:
        step.set_output(outputs)
        return
    step.set_output(outputs or None, error=f"exit {result.exit_code}: {result.stderr.strip()[:500]}")


async def _read_artifact(path: str | Path) -> str:
    """Read a skill artefact path — host-absolute via the filesystem, otherwise
    sandbox-relative via the active workspace."""
    p = Path(str(path))
    if p.is_absolute():
        return p.read_text(encoding="utf-8")
    return await Workspace.require().read_text(p)


def parse_skill_outputs(stdout: str) -> Iterator[SkillOutput]:
    """Yield every ``<key>=<value>`` line in ``stdout`` (in order)."""
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if (match := _OUTPUT_LINE_RE.match(line)) is not None:
            yield SkillOutput(key=match.group(1), value=match.group(2).strip())


def _decode(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


__all__ = ["SkillEnvError", "SkillExecutor", "SkillOutput", "SkillResult", "parse_skill_outputs"]
