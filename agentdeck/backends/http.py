"""Shared CLI/uvicorn helpers for backends that serve a FastAPI app.

Backend entry points stay tiny — discovery never imports ``fastapi`` or
``uvicorn`` at module load. :func:`require_http_runtime` is the boundary
that surfaces a friendly error when those deps aren't installed.
"""

from __future__ import annotations

from importlib.util import find_spec
from typing import TYPE_CHECKING, Annotated

import typer

if TYPE_CHECKING:
    from fastapi import FastAPI


HostOpt = Annotated[str, typer.Option("--host", help="Bind address.")]
PortOpt = Annotated[int, typer.Option("--port", help="TCP port.")]
ReloadOpt = Annotated[bool, typer.Option("--reload/--no-reload", help="Enable uvicorn auto-reload (dev).")]

_HTTP_RUNTIME_MODULES = ("fastapi", "uvicorn")


def require_http_runtime(deps_hint: str = "uv sync --all-packages") -> None:
    """Raise ``typer.BadParameter`` if uvicorn + fastapi aren't installed."""
    missing = [m for m in _HTTP_RUNTIME_MODULES if find_spec(m) is None]
    if missing:
        raise typer.BadParameter(
            f"HTTP runtime deps not installed ({', '.join(missing)}). Run `{deps_hint}` and retry.",
        )


def with_path_prefix(app: FastAPI, prefix: str) -> FastAPI:
    """Mount ``app`` under ``prefix``; return unchanged when prefix is empty."""
    if not prefix:
        return app
    from fastapi import FastAPI as _FastAPI

    wrapper = _FastAPI(openapi_url=None)
    wrapper.mount(prefix, app)
    return wrapper


async def serve_uvicorn(app: FastAPI, *, host: str, port: int, reload: bool) -> None:
    """Run ``app`` under uvicorn until interrupted."""
    import uvicorn

    config = uvicorn.Config(app, host=host, port=port, reload=reload, log_level="info")
    await uvicorn.Server(config).serve()


def bind_url(host: str, port: int, prefix: str = "") -> str:
    """``http://host:port[/prefix]`` — trailing slash stripped from prefix."""
    return f"http://{host}:{port}{prefix.rstrip('/')}"


__all__ = [
    "HostOpt",
    "PortOpt",
    "ReloadOpt",
    "bind_url",
    "require_http_runtime",
    "serve_uvicorn",
    "with_path_prefix",
]
