# Control and Concurrency

Status: TBD

Pause, resume, answer, cancel, races, and safe points.

## Proposed runtime-interrupt decision

A runtime interrupt is a first-class external action that submits durable control intent to a run. The runtime delivers that intent at the action's defined delivery boundary, records its outcome durably, and never reports an effect before the effect happened. An interrupt is not a lifecycle state.

## Proposed runtime interrupts

| Interrupt | Short meaning | Delivery boundary | Lifecycle effect |
| --- | --- | --- | --- |
| `pause()` | Stop execution without ending the run. | A safe point while `running`; immediate while `waiting_answer`. | `running` becomes `paused`; `waiting_answer` becomes `paused_waiting_answer`. |
| `cancel()` | End the run permanently. | A safe point while `running`; immediate when already stopped. | Any non-terminal state becomes `cancelled`. |
| `inject(value)` | Deliver unsolicited application input without resolving a pending question. | A defined executor input boundary. | State remains unchanged. |

`inject(value)` is a future requirement. It must have a durable event, ordering, idempotency, and executor-consumption contract before implementation.

## Input suspension and continuation actions

- `ctx.ask(...)` is an in-run input suspension, not a runtime interrupt. It immediately moves the run to `waiting_answer`.
- `answer(value)` is a continuation action, not an interrupt. It moves `waiting_answer` to `running`, or stores the value as `paused_answer_ready` when the input gate is paused.
- `resume()` is a continuation action, not an interrupt. It moves `paused` to `running`, `paused_waiting_answer` to `waiting_answer`, or `paused_answer_ready` to `running` with the stored answer.

## Decisions

TBD
