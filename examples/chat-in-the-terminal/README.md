# Chat in the terminal

A workflow that asks the questions instead of answering them, driven from a terminal by
`agentdeck chat`. **No API key**: a `@workflow` is your own Python, so nothing here reaches a
model.

```text
.agentdeck/
└── workflows/shift_handover/workflow.py    # @workflow + ctx.ask(...)
```

The file's location is the registration: nothing imports `workflow.py`, and there is no catalog
file to add it to.

```bash
uv venv && source .venv/bin/activate
uv pip install agentdeck-sdk
agentdeck chat shift_handover
```

Run it from *this* directory: `Deck.from_project()` discovers `./.agentdeck`, so the working
directory is what picks the project.

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

- **`ctx.ask` suspends the run.** The workflow is not polling or holding a prompt open: the run
  reaches `WAITING_ANSWER`, the terminal renders the question, and answering resumes the same
  run. Close the terminal mid-question and the run is still waiting.
- **The options are enforced, not suggested.** Answer with something outside them and the run
  refuses it and stays answerable, which is why the terminal re-asks rather than failing.
- **`agentdeck chat <target>` takes any agent or workflow.** Omit the name only when the deck
  holds exactly one target; a name that does not exist fails before the first prompt.
- **The terminal is a binding, not a special case.** `agentdeck chat` is
  `deck.expose(Terminal.stdio(target=...)).serve()`, and the same run is reachable over HTTP from
  `Native.http()` at the same time.

Next: [Workflows](https://agentdecksdk.com/build-your-deck/workflows) ·
[Human in the loop](https://agentdecksdk.com/runs-and-control/human-input)
