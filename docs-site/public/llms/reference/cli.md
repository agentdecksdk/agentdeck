# CLI

Generated from [`agentdeck/cli.py`](https://github.com/agentdecksdk/agentdeck/blob/main/agentdeck/cli.py) by capturing each subcommand's own `--help` output  -  the same rendering a terminal would show, not a second hand-written copy of it. `make check` regenerates this page and fails if the result differs (`scripts/generate_docs_reference.py`).

Two commands, neither a second way to do something a binding already does. `agentdeck chat` *is* the terminal binding: it serves `Terminal.stdio()` over the project's own deck, so the command is the shortcut and [Terminal](/bindings/terminal) is the contract. `agentdeck runs signal` is the out-of-band path, writing a control signal straight into the SQLite `ControlPort` without a served deck or an HTTP call.

## `agentdeck`

```text
usage: agentdeck [-h] {chat,runs} ...

positional arguments:
  {chat,runs}
    chat       a one-process terminal client over Terminal.stdio()

options:
  -h, --help   show this help message and exit
```

## `agentdeck chat`

```text
usage: agentdeck chat [-h] [target]

positional arguments:
  target      the agent or workflow to talk to; omit it when the deck holds one

options:
  -h, --help  show this help message and exit
```

## `agentdeck runs`

```text
usage: agentdeck runs [-h] {signal} ...

positional arguments:
  {signal}

options:
  -h, --help  show this help message and exit
```

## `agentdeck runs signal`

```text
usage: agentdeck runs signal [-h] --control-db CONTROL_DB [--reason REASON]
                             run_id {cancel,pause,resume}

positional arguments:
  run_id                the run to signal
  {cancel,pause,resume}
                        the verb - see Run Control for what each does

options:
  -h, --help            show this help message and exit
  --control-db CONTROL_DB
                        path to the ControlPort's SQLite file
  --reason REASON       why, recorded in the run's log with the request
```
