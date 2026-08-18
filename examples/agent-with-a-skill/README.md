# Agent with a skill

An agent with two tools and one skill. The tools are what it can *do*; the skill is prose that
shapes *how*  -  loaded only when the model decides it needs it.

```text
.agentdeck/
├── agents/handover_desk/agent.py     # two @function_tools + Agent(..., skills=["shift-notes"])
└── skills/shift-notes/SKILL.md       # frontmatter + instructions the model loads on demand
run.py                                # two turns on one session: a lookup, then a note
```

The directory name is the skill's name and must match the `name:` in its frontmatter. `description:`
is what the agent sees when deciding whether the skill applies  -  both are required, or
`Deck.build()` fails naming the bundle.

## Run it

```bash
uv venv && source .venv/bin/activate
uv pip install agentdeck-sdk
export OPENAI_MODEL=gpt-4.1-mini OPENAI_API_KEY=sk-...
python run.py
```

Run it from *this* directory: `Deck.from_project()` discovers `./.agentdeck`, so the working
directory is what picks the project. Filing a note writes `handover_notes.json` here  -  that file is
the observable side effect, so delete it between runs if you want a clean one.

`OPENAI_BASE_URL` points it at any OpenAI-compatible server instead  -  a gateway, vLLM, Ollama.
Chat-Completions-only servers also want `OPENAI_USE_RESPONSES=false`.

## What to look at

- **The skill is not a tool.** `skills=[...]` puts its `description` in the agent's instructions;
  the model then calls `load_skill("shift-notes")` to read the prose, and only then writes the note.
  Watch the run's `tool.call.started` events and you will see `load_skill` fire before
  `file_handover_note`.
- **The contract is prose plus a tool call, nothing typed.** SKILL.md says "ask before filing if
  the user has not said what is still open", and that is enforced by the model reading it  -  not by
  a schema. Never import a skill's own module from agent code.
- **The skill earns its place by changing the output.** Ask for a note without the skill and you get
  three invented sentences; with it, the model asks for what is missing first. That is the whole
  reason to reach for one instead of a longer `instructions` string.
- **`session_id` is what makes the second turn a follow-up.** Both `run` calls share one session, so
  the note turn already knows which shift was discussed.

Next: [Skills](https://sagi5060.github.io/agentdeck/concepts/skills) ·
[Add a Tool](https://sagi5060.github.io/agentdeck/guides/add-a-tool)
