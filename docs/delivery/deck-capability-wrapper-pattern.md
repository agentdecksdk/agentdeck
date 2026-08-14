# Capability Wrapper Pattern in `Deck`

`Deck(...)` stays small and declarative. Top-level executable components are passed directly; a
subsystem with its own discovery, loading, lifecycle or configuration behavior gets a dedicated
capability object that owns those options, instead of pushing them onto `Deck`.

```python
deck = Deck(
    agents=[booking_agent, support_agent],
    workflows=[booking_workflow],
    skills=Skills("./skills", validate=True),
    mcp=MCP("mcp.json"),
)
```

```text
Deck
├── executable roots
│   ├── agents=[...]
│   └── workflows=[...]
│
└── capability systems
    ├── skills=Skills(...)
    ├── mcp=MCP(...)
    └── future capability providers
```

Two kinds of argument: roots that execute, and capabilities that have behavior of their own.

## What a wrapper owns

`Skills(...)` owns skill directory paths, `SKILL.md` discovery, validation, indexing, progressive
loading, and any future source/load options. The subsystem grows there; the `Deck` constructor does
not.

## What does not get one

Do not wrap a simple first-class declaration only for symmetry. Avoid:

```python
Agents([...])
Workflows([...])
Tools([...])
```

unless those wrappers eventually own real subsystem behavior.

> **Rule:** keep `Deck` declarative; let capability objects own their own loading, validation,
> lifecycle, and configuration.
