"""Declarative ``BaseBackend`` — ClassVar surface for a foreign-protocol shim.

Subclasses set the ClassVars (``name``, ``description``, ``surface``); the
base auto-builds a matching :class:`typer.Typer` sub-app on ``cli`` so
backends don't have to repeat the same Typer construction. Decorate
commands directly on the class:

    class FooBackend(BaseBackend):
        name = "foo"
        description = "..."
        surface = "http"

    @FooBackend.cli.command("serve")
    def serve(...): ...

The framework mounts ``cli`` under ``agentdeck backend <name>`` automatically.
A subclass that needs custom Typer options can still override ``cli``
explicitly — the base only fills it in when missing.
"""

from __future__ import annotations

from typing import Any, ClassVar

import typer


class BaseBackend:
    """Discovery surface for a foreign-protocol backend."""

    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    surface: ClassVar[str] = ""
    cli: ClassVar[typer.Typer]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if not cls.name or "cli" in cls.__dict__:
            return
        cls.cli = typer.Typer(
            name=cls.name,
            help=cls.description or None,
            rich_markup_mode="rich",
            no_args_is_help=True,
        )


__all__ = ["BaseBackend"]
