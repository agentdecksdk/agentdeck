# Chat in the terminal

A workflow that asks the questions instead of answering them, driven from a terminal by
`agentdeck chat`. **No API key**: a `@workflow` is your own Python, so nothing here reaches a
model. [Source](https://github.com/agentdecksdk/agentdeck/tree/main/examples/chat-in-the-terminal).

```text
.agentdeck/
└── workflows/shift_handover/workflow.py    # @workflow + ctx.ask(...)
```

## The workflow

```python no-test reason="the example's own file, discovered from .agentdeck/ rather than imported"
from agentdeck import WorkflowCtx, workflow


@workflow
async def shift_handover(ctx: WorkflowCtx, area: str) -> str:
    """Write a handover note for one area, asking the outgoing operator what to flag."""
    severity = await ctx.ask(
        f"how did {area} run this shift?",
        options=["quiet", "busy", "problems"],
    )
    if severity == "problems":
        detail = await ctx.ask("what should the next shift look at first?")
        return f"{area}: problems. First: {detail}"
    return f"{area}: {severity}, nothing to escalate."
```

## Run it

```bash
uv pip install agentdeck-sdk
agentdeck chat shift_handover
```

```text
> bay 4
? how did bay 4 run this shift?
  1) quiet
  2) busy
  3) problems
> 3
? what should the next shift look at first?
> the conveyor stalled twice
-- run.completed --
>
```

Ctrl-C cancels a run in flight, or leaves the prompt if nothing is running. Ctrl-D exits.

## What to look at

- **`ctx.ask` suspends the run.** Nothing polls and no prompt is held open: the run reaches
  `WAITING_ANSWER`, the terminal renders the question, and answering resumes the same run. Close
  the terminal mid-question and the run is still waiting.
- **The options are enforced, not suggested.** An answer outside them is refused and the run stays
  answerable, which is why the terminal re-asks instead of failing.
- **`agentdeck chat <target>` takes any agent or workflow.** Omit the name only when the deck holds
  exactly one target; a name that does not exist fails before the first prompt.
- **The terminal is a binding, not a special case.** `agentdeck chat` is
  `deck.serve(Terminal.stdio(target=...))`, and the same run is reachable over
  [`Native.http()`](/bindings/native) at the same time.

Next: [Workflows](/build-your-deck/workflows) · [Terminal](/bindings/terminal)
