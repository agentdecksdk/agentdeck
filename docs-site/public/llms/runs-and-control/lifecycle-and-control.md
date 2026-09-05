# Lifecycle & Control

A run is in exactly one of six states. Status is folded from the event log rather than stored
beside it, so there is no second source that can disagree with what happened.

## The states

| Status | Meaning | Terminal |
|---|---|---|
| `running` | Executing now | no |
| `paused` | Stopped cooperatively at a safe point | no |
| `waiting_answer` | Parked on an interrupt, waiting for input | no |
| `completed` | Finished, with a result | yes |
| `failed` | Finished, with an error | yes |
| `cancelled` | Stopped and will not continue | yes |

There is no queued state. A run is `running` from the moment it starts.

```python
from agentdeck.core.status import RunStatus

RunStatus.RUNNING, RunStatus.PAUSED, RunStatus.WAITING_ANSWER
RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED
```

## What moves a run between them

Each lifecycle event sets exactly one status, which is why the log and the status can never
disagree.

| Event | Resulting status |
|---|---|
| `run.started` | `running` |
| `run.paused` | `paused` |
| `run.interrupted` | `waiting_answer` |
| `run.resumed` | `running` |
| `run.completed` | `completed` |
| `run.failed` | `failed` |
| `run.cancelled` | `cancelled` |

## What you can act on

`paused` and `waiting_answer` are the two suspended states, and each refuses the other's
operation: lift a pause with `resume()`, answer an interrupt with `answer(value)`. The three
terminal states accept nothing, and asking anyway returns quietly rather than raising.

```python
run = await deck.runs.start("Jack", question)

await run.pause()          # running -> paused, at the next safe point
await run.resume()         # paused -> running
await run.cancel()         # -> cancelled
await run.answer(value)    # waiting_answer -> running

status = await run.status()   # a coroutine, not a property
```

`pause` and `cancel` are requests, not interrupts. The run records the signal, then acts on it
when it next reaches a safe point: between stream items, before dispatching a tool, or at a node
boundary. Two events make that visible, `control.requested` when the signal is recorded and
`control.observed` when the run picks it up, so a control that has not taken effect yet is
distinguishable from one that was never seen.

### Capability matrix

Which operations are legal from each state:

| State | `pause()` | `resume()` | `cancel()` | `answer()` |
|---|---|---|---|---|
| `running` | ✓ | -- | ✓ | -- |
| `paused` | -- | ✓ | ✓ | -- |
| `waiting_answer` | ✓ | -- | ✓ | ✓ |
| `completed` / `failed` / `cancelled` | -- | -- | -- | -- |

`✓` is legal; `--` is not, and covers both a refusal that raises and a call that quietly does
nothing. `PRECONDITIONS` in `agentdeck/core/status.py` carries the exact verdict and reason
attached to each cell.

## Related

- [Runs](/runs-and-control/runs) - starting a run and getting the handle back
- [Pause / Resume](/runs-and-control/pause-resume) - safe points in more detail
- [Human Input](/runs-and-control/human-input) - answering a run parked at an interrupt
- [Events](/reference/events) - every event kind and its payload
