# Execution Tree

Status: Proposed canonical design

This document defines the runtime topology of one execution: nested Runs, branches, agents, workflows, tools, forks, parallel work, and the derived Run tree.

## Core model

Anything independently executable is represented by a Run identity.

A Run may create child Runs.

The execution graph exposed for observability is therefore rooted in a top-level Run and contains durable parent/child relationships.

Typical shapes include:

```text
workflow
  -> agent
      -> tool
```

```text
workflow
  -> agent
      -> workflow
          -> agent
              -> tool
```

```text
agent
  -> agent
      -> agent
```

and parallel combinations of all of the above.

## Identity

Every Run has a stable durable `run_id`.

Every child Run records its `parent_run_id`.

A repeated call to the same agent creates another Run identity.

For example:

```text
researcher -> R1
researcher -> R3
researcher -> R9
```

These are three distinct executions even though they target the same agent definition.

## Branches

A parent may launch multiple child Runs concurrently.

Conceptually:

```text
R0
├── R1
├── R2
└── R3
```

The tree view must not infer identity from target names.

It must use durable Run identity and parent relationships.

## Fork / parallel

Fork is an execution topology operation, not a Run lifecycle state.

A fork creates or coordinates multiple branches that may advance independently.

Example:

```text
R0 workflow
└── fork F1
    ├── R1 agent
    ├── R2 agent
    └── R3 workflow
```

A fork may be represented as explicit projection metadata or as a derived grouping over child start causation.

The event contract must carry enough causation information to rebuild the grouping deterministically if the grouping is part of the public view.

## Multiple simultaneous asks

Parallel branches may independently reach asks:

```text
R0
├── R1 -> ask-101
└── R2 -> ask-202
```

The execution tree therefore needs to expose asks at their origin node.

A top-level view may aggregate open asks, but the canonical identity remains attached to the originating branch.

## Example full tree

```text
Run R0: workflow "research_and_execute"
status: running

R0
├── R1: agent "planner"
│   status: completed
│   └── R1T1: tool "load_context"
│       status: completed
│
├── F1: parallel group
│   ├── R2: agent "researcher"
│   │   status: waiting_answer
│   │   ├── tool "web_search" -> completed
│   │   └── ask ask-101 -> open
│   │
│   ├── R3: agent "analyst"
│   │   status: running
│   │   └── R4: sub-agent "risk-agent"
│   │       status: paused
│   │
│   └── R5: agent "researcher"
│       status: cancelled
│       └── ask ask-202 -> answered before cancellation
│
└── R6: workflow "execution"
    status: paused_answer_ready
    └── R7: agent "executor"
        status: paused_answer_ready
        └── R8: sub-agent "reviewer"
            status: completed
```

## Node view

A projected Run-tree node should be able to expose:

```text
run_id
parent_run_id
target identity/name
target kind
lifecycle state
start sequence/time
terminal sequence/time if any
children
open asks
answered asks
pending/queued injections if exposed
control summary if exposed
```

Trace-heavy details such as token deltas need not be embedded in the node.

## Whole-tree questions

The runtime view should be able to answer:

- What is running now?
- What is paused?
- What is waiting for answers?
- Which asks are open?
- Which answers already arrived?
- Which branches completed?
- Which branches failed?
- Which branches were cancelled?
- Which nodes are children of which parent?
- Which nodes are currently concurrent?
- Which calls target the same agent definition?
- Which subtree is blocking parent progress?
- Which injections are still pending, if that is exposed?

## Parent state and child state

A parent's lifecycle state is not a lossy aggregate enum of all child states.

The parent has its own lifecycle state.

The tree projection separately exposes child states.

A parent waiting for children may be represented by its execution semantics without inventing synthetic lifecycle states unless the runtime contract explicitly introduces one.

## Causation

To rebuild the execution tree from the event log, events that create child Runs must durably identify:

- child Run identity;
- parent Run identity;
- causation or invocation identity where needed;
- target identity/kind.

## Invariants

1. Run identity, not target name, identifies a node.
2. Parent/child relationships are durable.
3. Parallel branches may hold different lifecycle states simultaneously.
4. Multiple asks may be open simultaneously across branches.
5. The full tree is derivable from the event log.
6. The projected tree is not a second source of truth.
