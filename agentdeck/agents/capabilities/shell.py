"""Shell capability — wraps SDK ``Shell`` with caps + structured truncation."""

from __future__ import annotations

import re
from typing import Any

from agents.sandbox.capabilities import Shell, ShellToolSet
from agents.sandbox.session import pty_types

from agentdeck.runtime.settings import LayeredSettings, settings_config

_TRUNCATED_PREFIX = "<<<TRUNCATED_OUTPUT:"
_TRUNCATION_RE = re.compile(r"(?:\.{3}|…)?(\d+) tokens truncated(?:\.{3}|…)?")


class ShellSpec(LayeredSettings):
    """Host-side caps for sandbox ``exec_command`` (env: ``AGENTDECK_SHELL_*``, YAML: ``shell:``)."""

    model_config = settings_config("AGENTDECK_SHELL_")

    yield_floor_ms: int = 600_000
    max_output_tokens: int = 15_000

    def build(self) -> Shell:
        floor_ms, max_tokens = self.yield_floor_ms, self.max_output_tokens

        # Raise the SDK's static yield-time max so long commands don't hit
        # a partial-yield path before our floor kicks in.
        if floor_ms > pty_types.PTY_YIELD_TIME_MS_MAX:
            pty_types.PTY_YIELD_TIME_MS_MAX = floor_ms

        def configure(toolset: ShellToolSet) -> None:
            original = toolset.exec_command.run

            async def run(args: Any) -> str:
                return _mark_truncation(await original(_apply_caps(args, floor_ms, max_tokens)))

            toolset.exec_command.run = run

        return Shell(configure_tools=configure)


def _mark_truncation(value: Any) -> str:
    # The SDK signals middle-truncation as inline free text ("…N tokens
    # truncated…"). Replace it with an unambiguous block so the next turn
    # can reason about prefix/suffix without parsing natural language.
    text = value if isinstance(value, str) else str(value)
    if _TRUNCATED_PREFIX in text:
        return text
    return _TRUNCATION_RE.sub(
        lambda m: (
            f"\n\n{_TRUNCATED_PREFIX} omitted {m.group(1)} tokens at this exact point. "
            "Text before this marker is the prefix; text after it is the suffix. "
            "The omitted middle is unavailable in this tool result. "
            "Read the source in smaller chunks to recover it.>>>\n\n"
        ),
        text,
    )


def _apply_caps(args: Any, floor_ms: int, max_tokens: int) -> Any:
    updates: dict[str, int] = {}
    # Skip clamping for interactive PTY runs — they're user-facing.
    if not getattr(args, "tty", False) and getattr(args, "yield_time_ms", 0) < floor_ms:
        updates["yield_time_ms"] = floor_ms
    current = getattr(args, "max_output_tokens", None)
    if current is None or current > max_tokens:
        updates["max_output_tokens"] = max_tokens

    if not updates:
        return args
    if hasattr(args, "model_copy"):
        return args.model_copy(update=updates)
    for key, value in updates.items():
        setattr(args, key, value)
    return args


__all__ = ["ShellSpec"]
