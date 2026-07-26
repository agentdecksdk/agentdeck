"""Sandbox session ownership: one ``SandboxSession`` shared via ContextVar."""

from __future__ import annotations

import io
import logging
import os
from collections.abc import AsyncGenerator, Callable, Generator, Iterator, Mapping, Sequence
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents.run_config import SandboxRunConfig
from agents.sandbox import Manifest
from agents.sandbox.entries import BaseEntry, File
from agents.sandbox.manifest import Environment
from agents.sandbox.sandboxes.unix_local import UnixLocalSandboxClient
from agents.sandbox.session import SandboxSession
from agents.sandbox.session.manifest_ops import apply_entry_batch
from agents.sandbox.session.sandbox_client import BaseSandboxClient
from agents.sandbox.types import ExecResult
from agents.sandbox.workspace_paths import SandboxPathGrant

from agentdeck.runtime.capture import CAPTURE_ENV, Capture
from agentdeck.runtime.observability import sandbox_trace_env

logger = logging.getLogger(__name__)

INPUT_FILES_DIR = "input_files"
OUTPUT_FILES_DIR = "output_files"

# ``CAPTURE_ENV`` (imported above) is the wire contract for the per-session capture
# identity: the JSON of a core ``Capture`` (actor / author_id / session_id) the
# Workspace stamps onto everything its skills emit. The Workspace builds the
# Capture host-side (unspoofable in the sandbox) and injects it here; skills read
# it via ``skill_runtime.capture``. The env name lives in ``skill_runtime.capture``
# (the reader's side) so the two ends never drift. Per-content ``rationale`` is
# *not* injected: each skill adds its own.

# Every catalog skill writes its deliverables under
# ``output_files/<skill_snake_name>/<input_stem>/``. Keep new skills on the
# same shape so callers can predict paths without reading SKILL.md.

_active: ContextVar[Workspace | None] = ContextVar("toolkit_workspace", default=None)

# Ambient per-session capture identity. The runtime binds it ONCE at the session
# boundary (where it first knows who / which session is acting); every
# ``Workspace.open`` within the scope inherits it, so skills and agents never
# resolve the session id themselves — it rides into every emitted entity's
# ``Capture`` under the hood. Mirrors the ``_active`` workspace ContextVar.
_runtime_capture: ContextVar[Capture | None] = ContextVar("agentdeck_runtime_capture", default=None)


@contextmanager
def runtime_capture(capture: Capture | None) -> Generator[None, None, None]:
    """Bind the ambient capture identity for the enclosed run.

    Set once at the session boundary — e.g. a backend that knows the conversation
    id does ``with runtime_capture(Capture(actor=AGENT, session_id=sid)): ...`` —
    and every :meth:`Workspace.open` inside (across extract / chat / nested
    workflows) inherits it as the default ``capture``. An explicit
    ``Workspace.open(capture=...)`` still wins for a one-off override.
    """
    token = _runtime_capture.set(capture)
    try:
        yield
    finally:
        _runtime_capture.reset(token)


def current_capture() -> Capture | None:
    """The ambient session capture bound by :func:`runtime_capture`, or ``None``.

    The read side of the same ContextVar: whoever needs the acting identity (e.g.
    to tag a trace with its ``session_id``) reads it here instead of the private var.
    """
    return _runtime_capture.get()


@dataclass(slots=True)
class Workspace:
    """One sandbox session shared across nested agents / workflows / skills.

    ``open()`` inherits an outer workspace bound to the current async
    context, so nested calls share the workdir. Anything written under
    :data:`OUTPUT_FILES_DIR` is a deliverable served by the ACP layer at
    ``GET /artifacts/{sessionId}/files/...`` — use :meth:`write_output`
    rather than hard-coding the directory name.

    Catalog skills follow a uniform sub-layout inside ``output_files/``:
    ``output_files/<skill_snake_name>/<input_stem>/<file>``. The leading
    ``output_files/`` is the only host-observable contract; the per-skill
    sub-tree lets callers predict deliverable paths without reading each
    skill's ``SKILL.md``.
    """

    client: BaseSandboxClient[Any]
    session: SandboxSession
    _started: bool = False

    @classmethod
    @asynccontextmanager
    async def open(
        cls,
        *,
        client: BaseSandboxClient[Any] | None = None,
        client_factory: Callable[[], BaseSandboxClient[Any]] = UnixLocalSandboxClient,
        environment: Mapping[str, str] | None = None,
        input_files: Sequence[str | os.PathLike[str]] | None = None,
        manifest_root: str | os.PathLike[str] | None = None,
        extra_path_grants: Sequence[SandboxPathGrant] = (),
        capture: Capture | None = None,
    ) -> AsyncGenerator[Workspace, None]:
        """Open a session — nested calls reuse an outer one if present.

        Pinning ``manifest_root`` flips ``workspace_root_owned=False`` in
        the SDK so ``client.delete()`` skips the rmtree; the host dir
        survives and the API layer can serve ``output_files/`` straight
        off the host filesystem.

        ``extra_path_grants`` declares trusted host roots outside the SDK
        process base_dir (cwd) that ``LocalDir``/``LocalFile`` materialize
        from. When reusing an outer workspace, additional grants are
        merged into the live session manifest so nested skill mounts see
        them. Treat grants as trusted application configuration — never
        plumb them from model output.

        ``capture`` is the per-session identity (who/which-session) stamped
        onto everything the sandbox's skills emit, serialized into the
        :data:`CAPTURE_ENV` env var on the host — never sourced from model
        output. It defaults to the ambient :func:`runtime_capture` the session
        bound, so callers normally pass nothing; an explicit value is a one-off
        override.
        """
        # Identity falls back to the ambient session-bound capture, so a single
        # `runtime_capture(...)` at the session boundary reaches every open.
        # Serialize once; both the reuse and fresh-session branches consume the
        # merged dict. An explicit ``environment`` key still wins on collision.
        capture = capture if capture is not None else _runtime_capture.get()
        # Host-built, unspoofable-in-sandbox context: the capture identity and (when
        # Langfuse is on) the OTLP export target + W3C trace carriers of the active
        # span. A skill's ``skill_runtime`` reads both.
        injected: dict[str, str] = sandbox_trace_env()
        if capture is not None:
            injected[CAPTURE_ENV] = capture.model_dump_json(exclude_none=True)
        if injected:
            # Precedence: injected defaults < explicit caller env < host-owned trace
            # carriers. Carriers win last so a stale caller ``TRACEPARENT``/``BAGGAGE``
            # can't detach the sandbox from the current span.
            carriers = {k: injected[k] for k in ("TRACEPARENT", "BAGGAGE") if k in injected}
            environment = {**injected, **(dict(environment) if environment else {}), **carriers}
        if (active := _active.get()) is not None:
            active._merge_path_grants(extra_path_grants)
            active._merge_environment(environment)
            yield active
            return

        client = client or client_factory()
        manifest = _build_manifest(environment, input_files, manifest_root, extra_path_grants)
        session = await client.create(manifest=manifest, options=None)
        # No bound identity? The sandbox session id is only known post-create, so
        # stamp it onto the live manifest as a minimal fallback capture — ``exec``
        # re-resolves ``environment`` each call. An explicit ``environment`` key or a
        # bound ``capture`` already rode in via the manifest and is left intact.
        if CAPTURE_ENV not in session.state.manifest.environment.value:
            session.state.manifest.environment.value[CAPTURE_ENV] = Capture(
                session_id=str(session.state.session_id),
            ).model_dump_json(exclude_none=True)
        workspace = cls(client=client, session=session)
        token = _active.set(workspace)
        try:
            yield workspace
        finally:
            _active.reset(token)
            # Per-phase try/except so a teardown failure can't mask the body's
            # exception or skip the next phase. Failures are logged loudly.
            try:
                await session.aclose()
            except Exception:  # noqa: BLE001 — per-phase teardown; log loudly so caller's exception isn't masked and the next teardown phase still runs
                logger.exception("workspace teardown: session.aclose failed")
            try:
                await client.delete(session)
            except Exception:  # noqa: BLE001 — per-phase teardown; same justification as session.aclose above
                logger.exception("workspace teardown: client.delete failed")

    @classmethod
    def require(cls) -> Workspace:
        if (ws := _active.get()) is None:
            raise RuntimeError(
                "No active Workspace. Wrap the call in `async with Workspace.open(): ...`.",
            )
        return ws

    @property
    def sandbox_run_config(self) -> SandboxRunConfig:
        return SandboxRunConfig(client=self.client, session=self.session)

    async def materialize(self, entries: Sequence[tuple[Path, BaseEntry]]) -> None:
        await self._ensure_started()
        # NOTE: ``_manifest_base_dir`` is the only way to obtain the session's
        # workdir; the SDK does not expose a public accessor. See pyproject's
        # ``reportPrivateUsage = "warning"`` carve-out for cross-package _helpers.
        await apply_entry_batch(self.session, list(entries), base_dir=self.session._manifest_base_dir())

    async def read_text(self, path: str | Path, encoding: str = "utf-8") -> str:
        await self._ensure_started()
        stream = await self.session.read(Path(path))
        try:
            return stream.read().decode(encoding)
        finally:
            stream.close()

    async def write_bytes(self, path: str | Path, content: bytes) -> None:
        await self._ensure_started()
        rel = Path(path)
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError(f"write path must be relative and must not escape the sandbox: {path!r}")
        if rel.parent.parts and rel.parent != Path("."):
            await self.session.mkdir(rel.parent, parents=True)
        await self.session.write(rel, io.BytesIO(content))

    async def write_text(self, path: str | Path, content: str, *, encoding: str = "utf-8") -> None:
        await self.write_bytes(path, content.encode(encoding))

    async def exec(self, *cmd: str | Path, timeout: float | None = None, shell: bool = False) -> ExecResult:
        await self._ensure_started()
        return await self.session.exec(*cmd, timeout=timeout, shell=shell)

    @staticmethod
    def output_path(name: str | Path) -> Path:
        """Sandbox-relative path under ``output_files/``."""
        rel = Path(name)
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError(f"output name must stay inside output_files/: {name!r}")
        return Path(OUTPUT_FILES_DIR) / rel

    async def write_output(
        self,
        name: str | Path,
        content: str | bytes,
        *,
        encoding: str = "utf-8",
    ) -> Path:
        path = self.output_path(name)
        if isinstance(content, bytes):
            await self.write_bytes(path, content)
        else:
            await self.write_text(path, content, encoding=encoding)
        return path

    async def read_output(self, name: str | Path, *, encoding: str = "utf-8") -> str:
        return await self.read_text(self.output_path(name), encoding=encoding)

    async def _ensure_started(self) -> None:
        # SDK Runner starts on the first turn, but direct IO callers may
        # hit the session before any agent turn fires.
        if self._started:
            return
        if not await self.session.running():
            await self.session.start()
        self._started = True

    def _merge_path_grants(self, grants: Sequence[SandboxPathGrant]) -> None:
        if not grants:
            return
        manifest = self.session.state.manifest
        existing = manifest.extra_path_grants
        added = tuple(g for g in grants if g not in existing)
        if added:
            manifest.extra_path_grants = (*existing, *added)

    def _merge_environment(self, environment: Mapping[str, str] | None) -> None:
        """Fold env into the active manifest so the next ``exec`` sees it.

        The Unix sandbox resolves ``manifest.environment`` on every exec
        (``_resolved_exec_context``), so mutating ``value`` after session
        creation propagates without a session restart. Later writers win
        on key collisions — matches the precedence of a fresh
        ``Workspace.open(environment=...)``.
        """
        if not environment:
            return
        self.session.state.manifest.environment.value.update(environment)


def input_file_entries(
    input_files: Sequence[str | os.PathLike[str]] | None,
) -> Iterator[tuple[Path, BaseEntry]]:
    """Yield ``(target, File)`` manifest entries for each host path."""
    seen: set[str] = set()
    for raw in input_files or ():
        path = Path(raw).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        if path.name in seen:
            raise ValueError(f"Duplicate input_files basename: {path.name}")
        seen.add(path.name)
        yield Path(f"{INPUT_FILES_DIR}/{path.name}"), File(content=path.read_bytes())


def _build_manifest(
    environment: Mapping[str, str] | None,
    input_files: Sequence[str | os.PathLike[str]] | None,
    manifest_root: str | os.PathLike[str] | None,
    extra_path_grants: Sequence[SandboxPathGrant],
) -> Manifest:
    entries: dict[str | Path, BaseEntry] = {str(t): e for t, e in input_file_entries(input_files)}
    kwargs: dict[str, Any] = {
        "environment": Environment(value=dict(environment or {})),
        "entries": entries,
        "extra_path_grants": tuple(extra_path_grants),
    }
    if manifest_root is not None:
        kwargs["root"] = str(Path(manifest_root))
    return Manifest(**kwargs)


__all__ = [
    "CAPTURE_ENV",
    "OUTPUT_FILES_DIR",
    "Workspace",
    "current_capture",
    "input_file_entries",
    "runtime_capture",
]
