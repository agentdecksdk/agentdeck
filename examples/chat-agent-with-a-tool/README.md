# Chat agent with a tool

An agent that answers questions about orders, and a plain Python function it calls to look one
up. Six lines of declaration; no JSON schema, no call-parsing, no tool loop.

```text
.agentdeck/
└── agents/order_desk/agent.py    # @function_tool + Agent(name="OrderDesk", tools=[...])
run.py                            # deck.run("OrderDesk", "where is order A-1001?")
```

The file's location is the registration — nothing imports `agent.py`, and there is no catalog
file to add it to.

## Run it

```bash
uv venv && source .venv/bin/activate
uv pip install "agentdeck @ git+https://github.com/agentdecksdk/agentdeck.git@v3.0.1"
export OPENAI_MODEL=gpt-4.1-mini OPENAI_API_KEY=sk-...
python run.py
```

Run it from *this* directory: `Deck.from_project()` discovers `./.agentdeck`, so the working
directory is what picks the project.

`OPENAI_BASE_URL` points it at any OpenAI-compatible server instead — a gateway, vLLM, Ollama.
Chat-Completions-only servers also want `OPENAI_USE_RESPONSES=false`.

## What to look at

- **`@function_tool`** is the Agents SDK's, not agentdeck's. It reads the parameter schema off
  the type hints and the description off the docstring; `tools=[...]` hands the list straight
  through. agentdeck does not sit between the model and your function.
- **The instructions tell the model to call the tool rather than guess.** That is the whole
  contract — there is no rule engine enforcing it.
- **The tool runs in this process with these privileges**, on arguments the model chose. Keep
  anything destructive behind a human approval instead (the sibling example), not behind an
  agent's judgement.

Next: [Add a Tool](https://agentdecksdk.com/guides/add-a-tool) ·
[Agents](https://agentdecksdk.com/concepts/agents)
