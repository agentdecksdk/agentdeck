"""core imports stdlib and pydantic only.

The import-linter contract is a denylist and only names what exists today; this asserts
the rule itself, so a module added later can't quietly widen it.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "agentdeck" / "core"
ALLOWED_ROOTS = sys.stdlib_module_names | {"pydantic", "agentdeck"}


def _imported_modules(source: str) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module)
    return found


def test_core_imports_stdlib_and_pydantic_only():
    for path in sorted(CORE.rglob("*.py")):
        for module in _imported_modules(path.read_text()):
            root = module.split(".")[0]
            assert root in ALLOWED_ROOTS, f"{path.name} imports {module}"
            assert not module.startswith("agentdeck.") or module.startswith("agentdeck.core"), (
                f"{path.name} imports {module}  -  core is the innermost ring"
            )
