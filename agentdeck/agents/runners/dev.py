"""Streamed terminal REPL runner for local agent development."""

from __future__ import annotations

from dataclasses import dataclass

from agentdeck.agents.runners.base import BaseRunner


@dataclass(slots=True)
class DevRunner(BaseRunner):
    """Interactive streamed REPL backed by a local sandbox session."""

    show_tool_output: bool = False
    tool_output_chars: int = 1_500

    async def run(self) -> None:
        # Imported lazily to keep prompt-toolkit / Rich out of the import path
        # for callers that only use HeadlessRunner.
        from backends.cli.repl import run_repl

        await run_repl(self, initial_agent_name=self.agent.name, show_tool_output=self.show_tool_output)


__all__ = ["DevRunner"]
