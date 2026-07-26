"""Generic plugin registry: discover ``T`` subclasses in a package's bundles."""

from __future__ import annotations

import importlib
import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Generic, TypeVar

T = TypeVar("T")

# ``skills/`` is reserved for project-side skill bundles colocated with agents.
RESERVED_DIRS: frozenset[str] = frozenset({"skills", "__pycache__"})


@dataclass(slots=True)
class PluginRegistry(Generic[T]):
    """Discover ``T`` subclasses in ``<package>/<bundle>/<module_name>.py``.

    Lazy: discovery runs on the first :meth:`list` call and is cached on
    the instance. Pass ``refresh=True`` to force a re-scan.
    """

    package: str
    base_class: type[T]
    module_name: str
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
            raise KeyError(f"No {self.label} named {name!r}. Available: {sorted(plugins)}.") from None

    def pick(self, name: str | None) -> type[T]:
        """Resolve one entry by name, or the sole entry when ``name`` is unset.

        Raises :class:`LookupError` whenever the input is empty, ambiguous, or
        unknown — callers decide whether that becomes an HTTP 4xx, a Typer
        exit, or a Rich panel.
        """
        plugins = self.list()
        where = f" under {self.package!r}"
        if not plugins:
            raise LookupError(f"No {self.label}s discovered{where}.")
        if name is None:
            if len(plugins) == 1:
                return next(iter(plugins.values()))
            raise LookupError(
                f"Multiple {self.label}s available{where}: {sorted(plugins)}. Pin one explicitly.",
            )
        try:
            return plugins[name]
        except KeyError as exc:
            raise LookupError(
                f"No {self.label} named {name!r}{where}. Available: {sorted(plugins)}.",
            ) from exc

    def _scan(self) -> dict[str, type[T]]:
        root = _package_dir(self.package)
        if root is None or not root.is_dir():
            return {}
        found: dict[str, type[T]] = {}
        for child in sorted(root.iterdir()):
            if not self._is_bundle(child):
                continue
            module = importlib.import_module(f"{self.package}.{child.name}.{self.module_name}")
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

    def _is_bundle(self, path: Path) -> bool:
        return (
            path.is_dir()
            and path.name not in RESERVED_DIRS
            and not path.name.startswith(("_", "."))
            and (path / f"{self.module_name}.py").is_file()
        )


def _package_dir(package: str) -> Path | None:
    spec = importlib.util.find_spec(package)
    if spec is None or not spec.submodule_search_locations:
        return None
    return Path(next(iter(spec.submodule_search_locations)))


__all__ = ["PluginRegistry"]
