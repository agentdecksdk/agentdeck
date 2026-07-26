"""Generic plugin registry: discover ``T`` subclasses in a package's bundles."""

from __future__ import annotations

import importlib
import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Generic, TypeVar

from agentdeck.errors import ConfigError, NotFoundError

T = TypeVar("T")


@dataclass(slots=True)
class PluginRegistry(Generic[T]):
    """Discover ``T`` subclasses in ``<package>/<type_dir>/<bundle>/<module_name>.py``.

    Lazy: discovery runs on the first :meth:`list` call and is cached on
    the instance. Pass ``refresh=True`` to force a re-scan.
    """

    package: str
    base_class: type[T]
    module_name: str
    type_dir: str
    label: str = "plugin"
    _cache: dict[str, type[T]] | None = field(default=None, init=False, repr=False)

    def list(self, *, refresh: bool = False) -> dict[str, type[T]]:
        """Return the discovered bundles. The result is the live cache — treat
        it as read-only; mutations corrupt subsequent lookups.
        """
        if refresh or self._cache is None:
            self._cache = self._scan()
        return self._cache

    def get(self, name: str) -> type[T]:
        plugins = self.list()
        try:
            return plugins[name]
        except KeyError:
            raise NotFoundError(f"No {self.label} named {name!r}. Available: {sorted(plugins)}.") from None

    def _scan(self) -> dict[str, type[T]]:
        project_root = _package_dir(self.package)
        if project_root is None or not project_root.is_dir():
            return {}
        root = project_root / self.type_dir
        if not root.is_dir():
            self._reject_legacy_layout(project_root)
            return {}
        found: dict[str, type[T]] = {}
        for child in sorted(root.iterdir()):
            if not self._is_bundle(child):
                continue
            module = importlib.import_module(f"{self.package}.{self.type_dir}.{child.name}.{self.module_name}")
            # ``attr.__module__ == module.__name__`` filters re-exports —
            # only classes defined in this module are registered.
            for attr in vars(module).values():
                if (
                    isinstance(attr, type)
                    and issubclass(attr, self.base_class)
                    and attr is not self.base_class
                    and attr.__module__ == module.__name__
                ):
                    found[attr.__name__] = attr
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


def _package_dir(package: str) -> Path | None:
    spec = importlib.util.find_spec(package)
    if spec is None or not spec.submodule_search_locations:
        return None
    return Path(next(iter(spec.submodule_search_locations)))


__all__ = ["PluginRegistry"]
