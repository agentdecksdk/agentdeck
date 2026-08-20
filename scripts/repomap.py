#!/usr/bin/env python3
"""Compact public-API map of agentdeck, read by agents before writing new code.

One line per public symbol with its signature, grouped by module, so a reuse
analysis can answer "does an abstraction for this already exist" from one grep
instead of a repo crawl. Run with: uv run scripts/repomap.py
"""

from __future__ import annotations

import griffe


def _sig(func: griffe.Function) -> str:
    params = []
    for p in func.parameters:
        part = str(p.name)
        if p.annotation is not None:
            part += f": {p.annotation}"
        if p.default is not None:
            part += f" = {p.default}"
        params.append(part)
    returns = f" -> {func.returns}" if func.returns is not None else ""
    prefix = "async def" if "async" in func.labels else "def"
    return f"{prefix} {func.name}({', '.join(params)}){returns}"


def _doc(obj: griffe.Object) -> str:
    if obj.docstring is None:
        return ""
    first = obj.docstring.value.strip().splitlines()[0]
    return f"  # {first[:100]}"


def _own_public(obj: griffe.Object) -> list[griffe.Object]:
    members = [m for m in obj.members.values() if not m.is_alias and not m.name.startswith("_")]
    return sorted(members, key=lambda m: m.lineno or 0)


def _print_module(mod: griffe.Module) -> None:
    lines: list[str] = []
    for member in _own_public(mod):
        if member.is_class:
            bases = f"({', '.join(str(b) for b in member.bases)})" if member.bases else ""
            lines.append(f"  class {member.name}{bases}{_doc(member)}")
            lines.extend(f"    {_sig(m)}{_doc(m)}" for m in _own_public(member) if m.is_function)
        elif member.is_function:
            lines.append(f"  {_sig(member)}{_doc(member)}")
        elif member.is_attribute:
            annotation = f": {member.annotation}" if member.annotation is not None else ""
            lines.append(f"  {member.name}{annotation}")
    if lines:
        print(f"{mod.relative_filepath}{_doc(mod)}")
        print("\n".join(lines))
    for sub in mod.members.values():
        if not sub.is_alias and sub.is_module and not sub.name.startswith("_"):
            _print_module(sub)


if __name__ == "__main__":
    _print_module(griffe.load("agentdeck"))
