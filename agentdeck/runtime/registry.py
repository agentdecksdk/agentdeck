"""Generic plugin registry: discover ``T`` instances in a package's bundles."""

from __future__ import annotations

import importlib
import importlib.util
import sys
import types
from dataclasses import dataclass, field
from importlib.machinery import ModuleSpec
from pathlib import Path
from typing import Generic, TypeVar

from agentdeck.errors import ConfigError, NotFoundError

T = TypeVar("T")

PROJECT_DIR = ".agentdeck"
_PROJECT_ALIAS = "agentdeck_project"


@dataclass(slots=True)
class PluginRegistry(Generic[T]):
    """Discover ``T`` instances in ``<package>/<type_dir>/<bundle>/<module_name>.py``.

    Lazy: discovery runs on the first :meth:`list` call and is cached on
    the instance. Pass ``refresh=True`` to force a re-scan. Two *different* bundles
    that declare an invocable of the same name raise ``ConfigError`` naming both bundle
    paths — one name is one invocable, never a silent shadow in bundle-sort order.
    A single bundle exposing the same instance under two names (e.g. an alias kept
    after a rename) is not a collision: it is the same object claiming its name twice.

    ``base_class`` is matched by ``isinstance`` — a bundle module holds one or more
    already-constructed ``Agent``/``Workflow`` instances (``authoring``'s ``Agent(...)``,
    ``Workflow(...)``), not subclasses to discover.
    """

    package: str
    base_class: type[T]
    module_name: str
    type_dir: str
    label: str = "plugin"
    _cache: dict[str, T] | None = field(default=None, init=False, repr=False)

    def list(self, *, refresh: bool = False) -> dict[str, T]:
        """Return the discovered bundles. The result is the live cache — treat
        it as read-only; mutations corrupt subsequent lookups.
        """
        if refresh or self._cache is None:
            self._cache = self._scan()
        return self._cache

    def get(self, name: str) -> T:
        plugins = self.list()
        try:
            return plugins[name]
        except KeyError:
            raise NotFoundError(f"No {self.label} named {name!r}. Available: {sorted(plugins)}.") from None

    def _scan(self) -> dict[str, T]:
        project_root = _package_dir(self.package)
        if project_root is None or not project_root.is_dir():
            return {}
        root = project_root / self.type_dir
        if not root.is_dir():
            self._reject_legacy_layout(project_root)
            return {}
        found: dict[str, T] = {}
        bundle_of: dict[str, str] = {}  # invocable name -> bundle dir that claimed it, for the collision message
        for child in sorted(root.iterdir()):
            if not self._is_bundle(child):
                continue
            module_path = f"{self.package}.{self.type_dir}.{child.name}.{self.module_name}"
            try:
                module = importlib.import_module(module_path)
            except Exception as exc:
                bundle_file = f"{self.type_dir}/{child.name}/{self.module_name}.py"
                raise ConfigError(f"{bundle_file} failed to import: {exc}") from exc
            for attr in vars(module).values():
                if not isinstance(attr, self.base_class):
                    continue
                name = attr.name
                # Identity, not name: ``vars(module)`` yields one entry per *binding*, so a
                # bundle aliasing its own instance under a second name (kept after a rename)
                # must not trip this against itself — only a second, different instance
                # claiming a name already taken is the collision.
                claimant = found.get(name)
                if claimant is not None and claimant is not attr:
                    raise ConfigError(
                        f"two bundles under '{self.type_dir}/' both define the {self.label} "
                        f"{name!r}: '{self.type_dir}/{bundle_of[name]}' and "
                        f"'{self.type_dir}/{child.name}'; one name is one invocable — rename one of them."
                    )
                found[name] = attr
                bundle_of[name] = child.name
        return found

    def _reject_legacy_layout(self, project_root: Path) -> None:
        # pre-0.3 layout put bundles straight under the project root instead of type_dir/.
        if any((c / f"{self.module_name}.py").is_file() for c in project_root.iterdir() if c.is_dir()):
            raise ConfigError(
                f"old .agentdeck layout detected: move '<bundle>/{self.module_name}.py' "
                f"under '.agentdeck/{self.type_dir}/<bundle>/{self.module_name}.py'."
            )

    def _is_bundle(self, path: Path) -> bool:
        return path.is_dir() and not path.name.startswith(("_", ".")) and (path / f"{self.module_name}.py").is_file()


def mount_project_dir(root: str | Path = PROJECT_DIR) -> str:
    """Make a project dir importable as package ``agentdeck_project``; returns that name.

    Defaults to ``./.agentdeck`` (``root=PROJECT_DIR``); ``Deck.from_project(path)`` passes an
    explicit ``path`` instead. One alias slot, so one mounted project per process at a time —
    matching the plan's own ruling that a deck-per-tenant is a process-per-tenant.

    A hidden dir can't be imported by name, so we register a synthetic parent
    package whose ``__path__`` points at it; the normal import machinery then
    resolves ``<alias>.agents.<bundle>.agent`` as namespace packages — no
    ``__init__.py`` needed anywhere under the project dir.
    """
    resolved = Path(root).resolve()
    if not resolved.is_dir():
        raise FileNotFoundError(f"project dir not found: {resolved}")
    module = types.ModuleType(_PROJECT_ALIAS)
    module.__path__ = [str(resolved)]
    module.__spec__ = ModuleSpec(_PROJECT_ALIAS, None, is_package=True)
    module.__spec__.submodule_search_locations = [str(resolved)]
    sys.modules[_PROJECT_ALIAS] = module
    return _PROJECT_ALIAS


def _package_dir(package: str) -> Path | None:
    spec = importlib.util.find_spec(package)
    if spec is None or not spec.submodule_search_locations:
        return None
    return Path(next(iter(spec.submodule_search_locations)))


__all__ = ["PROJECT_DIR", "PluginRegistry", "mount_project_dir"]
