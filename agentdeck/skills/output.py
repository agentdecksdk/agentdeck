"""Typed skill outputs — a Pydantic schema next to ``SKILL.md`` describing stdout.

Bundles declare the schema in frontmatter
(``metadata.output_schema: "output.py:DocumentParserOutput"``); workflows
read it via :attr:`SkillBundle.output_schema`. The default
:meth:`SkillOutputSchema.from_result` lifts each field from the matching
``key=value`` stdout line. Schemas are workflow-side — agents only see the
``SKILL.md`` prose and ``key=value`` lines.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Self

from pydantic import BaseModel

from agentdeck.skills.executor import SkillResult


class SkillOutputSchema(BaseModel):
    """Base class for a skill's typed, workflow-facing output."""

    @classmethod
    def from_result(cls, result: SkillResult) -> Self:
        """Lift each declared field from ``result.output(name)``."""
        kwargs = {n: v for n in cls.model_fields if (v := result.output(n)) is not None}
        return cls.model_validate(kwargs)


def load_schema(path: Path | str, class_name: str) -> type[SkillOutputSchema]:
    """Load ``class_name`` from the Python file at ``path``.

    Prefers canonical dotted-name import when ``src`` lives in a package
    on ``sys.path`` — that way the same class identity is shared with
    direct imports. Falls back to a synthetic file-path load.
    """
    src = Path(path).resolve()
    if not src.is_file():
        raise FileNotFoundError(f"output schema module not found: {src}")

    module = _import_canonical(src) or _import_from_file(src)
    cls: Any = getattr(module, class_name, None)
    if cls is None:
        raise ImportError(f"{src}: no attribute {class_name!r}")
    if not (isinstance(cls, type) and issubclass(cls, SkillOutputSchema)):
        raise TypeError(f"{src}:{class_name} must subclass SkillOutputSchema (got {cls!r}).")
    return cls


def _import_canonical(src: Path) -> ModuleType | None:
    """Resolve ``src`` to its canonical dotted name and import it.

    Walks up through both regular packages (``__init__.py`` present) and
    namespace packages (no ``__init__.py``) until the parent directory is
    on ``sys.path``. Without the namespace step, a layout like
    ``dev/examples/skills/<bundle>/output.py`` (where ``dev/`` is a
    namespace package because it has no ``__init__.py``) would be loaded
    as ``examples.skills.<bundle>.output`` and fail to import — the
    fallback file-path loader would then create a *second* class with
    the same name but distinct identity, breaking Pydantic ``isinstance``
    checks against statically-imported uses of the schema.
    """
    sys_paths = {Path(p).resolve() for p in sys.path if p}
    parts = [src.stem]
    parent = src.parent.resolve()
    while parent not in sys_paths and parent != parent.parent:
        parts.append(parent.name)
        parent = parent.parent
    if parent not in sys_paths or len(parts) == 1:
        return None
    try:
        return importlib.import_module(".".join(reversed(parts)))
    except ImportError:
        return None


def _import_from_file(src: Path) -> ModuleType:
    # Last three path parts disambiguate fixtures that share a stem.
    name = "_skill_module_" + "_".join(p.replace(".", "_").replace("-", "_") for p in src.parts[-3:])
    if (cached := sys.modules.get(name)) is not None and getattr(cached, "__file__", None) == str(src):
        return cached
    spec = importlib.util.spec_from_file_location(name, src)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {src}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


__all__ = ["SkillOutputSchema", "load_schema"]
