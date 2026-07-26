"""Filesystem capability spec — swaps ``apply_patch`` for a chat-completions shim."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agents import function_tool
from agents.sandbox.capabilities import Filesystem
from agents.sandbox.capabilities.tools.apply_patch_tool import SandboxApplyPatchTool
from agents.sandbox.session.base_sandbox_session import BaseSandboxSession
from agents.sandbox.types import User
from agents.tool import FunctionTool
from agents.tool_context import ToolContext
from pydantic import BaseModel, ConfigDict

# SDK ships ``apply_patch`` as a ``CustomTool`` (freeform grammar) which
# chat-completions rejects. We wrap the same underlying invoker in a
# ``FunctionTool`` and trim the bloated freeform-grammar description so
# small chat-completions models with strict json_schema don't degenerate.
_APPLY_PATCH_DESCRIPTION = (
    "Edit files inside the sandbox workspace via a unified-diff patch.\n"
    'Argument: {"patch": "<envelope>"} where envelope is wrapped in '
    "*** Begin Patch / *** End Patch and contains one or more "
    "*** Add File: <path>, *** Update File: <path>, or *** Delete File: <path> "
    "blocks. Use leading + / - / space on each diff line. Paths are workspace-relative."
)


class FilesystemSpec(BaseModel):
    """Filesystem capability with a chat-completions ``apply_patch`` shim.

    ``configure_tools`` runs after the shim swap; use it to disable tools
    or attach custom callbacks just like the SDK's hook.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    configure_tools: Callable[..., Any] | None = None

    def build(self) -> Filesystem:
        user_configure = self.configure_tools

        def configure(toolset: Any) -> None:
            original = toolset.apply_patch
            user = getattr(getattr(original, "editor", None), "user", None)
            toolset.apply_patch = _apply_patch_function_tool(original.session, user=user)
            if user_configure is not None:
                user_configure(toolset)

        return Filesystem(configure_tools=configure)


def _apply_patch_function_tool(
    session: BaseSandboxSession,
    *,
    user: str | User | None = None,
) -> FunctionTool:
    inner = SandboxApplyPatchTool(session=session, user=user)

    @function_tool(
        name_override="apply_patch",
        description_override=_APPLY_PATCH_DESCRIPTION,
        use_docstring_info=False,
    )
    async def apply_patch(ctx: ToolContext[Any], patch: str) -> str:
        result = await inner.on_invoke_tool(ctx, patch)
        return result if isinstance(result, str) else str(result)

    return apply_patch


__all__ = ["FilesystemSpec"]
