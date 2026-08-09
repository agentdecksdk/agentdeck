# Capability Wrapper Pattern in `Deck`

## Purpose

`Deck(...)` should stay small and focused.

Top-level executable components such as agents and workflows can be passed directly:

```python
deck = Deck(
    agents=[booking_agent, support_agent],
    workflows=[booking_workflow],
)
```

But subsystems that have their own discovery, loading, lifecycle, or configuration behavior should be represented by a dedicated capability object.

## Pattern

Prefer:

```python
deck = Deck(
    agents=[booking_agent],
    workflows=[booking_workflow],
    skills=Skills("./skills"),
    mcp=MCP("mcp.json"),
)
```

instead of pushing subsystem-specific options directly into `Deck`.

The wrapper owns the subsystem behavior; `Deck` only composes it.

## Example: Skills

```python
skills = Skills(
    "./skills",
    validate=True,
)

deck = Deck(
    agents=[booking_agent],
    skills=skills,
)
```

`Skills(...)` can own:

- skill directory paths
- `SKILL.md` discovery
- validation
- indexing
- progressive loading
- future source/load options

This lets the subsystem grow without bloating the `Deck` constructor.

## General Rule

Use a dedicated wrapper when a capability has meaningful behavior beyond holding a value:

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

Do not wrap simple first-class declarations only for symmetry.

Avoid unnecessary APIs such as:

```python
Agents([...])
Workflows([...])
Tools([...])
```

unless those wrappers eventually own real subsystem behavior.

> **Rule:** keep `Deck` declarative; let capability objects own their own loading, validation, lifecycle, and configuration.
