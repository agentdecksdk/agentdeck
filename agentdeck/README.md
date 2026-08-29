# `agentdeck/`

Declarative harness for the [OpenAI Agents SDK](https://github.com/openai/openai-agents-python)
and native workflows.

agentdeck owns **configuration and orchestration**: settings, tools and MCP servers, skill disclosure,
native workflow execution, plug-in discovery.

```text
agentdeck/
    core/          # event schema and ports: stdlib + pydantic only
    runtime/       # settings, plugin discovery, the Runtime's own primitives
    authoring/     # Agent, @tool, @workflow: the declarative construction API, compiles to InvocableSpec
    skills/        # Skills: SKILL.md discovery, validation, disclosure text
    adapters/      # executors (openai-agents, native), event stores, control ports, tool sources
    adapters/bindings/  # protocol/channel/surface translation over the gateway (Native, Terminal, ...)
    deck.py        # Deck: the composition root: build a catalog, open it, run turns on it
    mcp.py         # MCP: .mcp.json parsing and validation
```

`Deck` is the one class application code needs directly: see the top-level
[README](../README.md) for how to use it, and [`docs-site/`](../docs-site/) for the full guide.
The rest of this package is composition plumbing `Deck` wires together; nothing else here is
meant to be imported on its own except `agentdeck.authoring` (`Agent`, `@workflow`, `@tool`).

## Plug-in discovery

`agentdeck.runtime.registry.PluginRegistry` walks `<package>/<type_dir>/<bundle>/<module>.py` and
indexes every module-level *instance* of a base class (`Agent`, and the definition a
`@workflow` produces)  -  not subclasses; an `Agent` is a value, not something to subclass.
`Deck.from_project()` builds one
registry each for `agents/` and `workflows/`. A pre-0.3 project dir without the `agents/`/
`workflows/` type subdirectory raises a `ConfigError` pointing at the current layout instead of
silently discovering nothing.

## Settings

Layered Pydantic-Settings models. See [`runtime/settings.py`](runtime/settings.py) for
definitions, or the generated `docs-site/content/reference/settings.mdx` for every
`AGENTDECK_*`/`OPENAI_*`/`TAVILY_*` env var.

```python
from agentdeck.runtime.settings import get_settings

s = get_settings()  # cached
s.openai.model
s.runner.max_turns
s.events.url  # e.g. "sqlite://.agentdeck/events.sqlite3"  -  the scheme names the backend
```
