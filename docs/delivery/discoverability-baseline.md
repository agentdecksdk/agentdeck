# Discoverability baseline — 2026-08-12

**Status:** measured · **Repo:** `agentdecksdk/agentdeck` at `453f06c` · **Release:** v3.0.1

The "before" reading for `plan-adoption.md`. Taken on the day the canonical domain, the
organisation move and the first Context7 submission all landed, so it measures the state
*immediately after* the infrastructure work and *before* any of the writing.

The question it has to answer, from the plan:

> Someone who **does not know AgentDeck exists**, but describes a problem it solves, gets
> **AgentDeck SDK** back as one of the relevant answers.

## Result

| Channel | Returned | Coverage |
|---|---|---|
| **Context7** | **0 / 30** | all 30 questions, scripted against the search API |
| **GitHub repository search** | **1 / 30** | all 30 questions, `search/repositories` top 10 |
| **Web search** | **0 / 8** | the 8 highest-value questions |
| ChatGPT Search · Perplexity | not measured | no API available here — run by hand, see below |

**Zero, essentially everywhere.** That is the correct and expected reading for a project whose
domain was one day old and whose package is not on PyPI. It is a baseline, not a verdict.

### The one GitHub hit is not a real one

`one ordered event log per agent run` ranks **#1** — because that phrase is lifted verbatim from
the repository description. It matches a string, not a concept. Change the description and it
disappears.

### Context7 ranks the name, and only the name

| Query | Top 3 |
|---|---|
| `agentdeck` | **/sagi5060/agentdeck**, /agentdock/agentdock, /agentdeskai/browser-tools-mcp |
| `agentdeck sdk` | **/sagi5060/agentdeck**, /websites/ai-sdk_dev, /dotnet/sdk |
| `human in the loop` | /worldcoin/human-in-the-loop, /websites/loop, /mrkai77/loop |

So the entry is indexed and healthy. It is reachable by anyone who already knows the name, and by
nobody else — precisely the distinction the plan says not to confuse.

Note the entry measured here is `/sagi5060/agentdeck`, indexed **before** `context7.json` was
valid and therefore with no rules and no folder scoping. `upstash/context7#3027` asks for its
removal in favour of `/agentdecksdk/agentdeck`.

### The branded query fails

`AgentDeck SDK python agents workflows skills` returned OpenAI's Agents SDK and Google's ADK, and
the engine stated plainly:

> the specific "AgentDeck SDK" doesn't appear in these results

This is the sharpest number in the document. Not "ranks poorly" — **absent**.

## What the searches revealed that the numbers do not

Three findings that change what to write, which is the actual value of running this now.

**1. "Combine the OpenAI Agents SDK with LangGraph" is an unserved query.** Two independent
searches reported the gap in their own words:

> The search results don't contain specific guidance on integrating or using these two frameworks
> together as a combined system.

> The search results primarily compare frameworks […] as alternatives rather than wrappers around
> each other.

Every result is a *versus* article. Nobody is answering the question AgentDeck was built to
answer. That is an open lane, and it makes `use-your-existing-langgraph-agent` the highest-value
page to write — ahead of `/why-agentdeck`.

**2. The positioning has near-competitors worth knowing.** Two products already wrap rather than
replace, and neither surfaced in any earlier competitive pass:

- **NVIDIA NeMo Agent Toolkit** — a `langgraph_wrapper` workflow type, *"integrate existing
  LangGraph agents with minimal changes […] adding configuration management, observability, and
  evaluation"*. That is close to AgentDeck's sentence.
- **Agno AgentOS** — `LangGraphAgent` *"wraps a compiled LangGraph graph so it can be served
  through AgentOS or used standalone."*

Neither wraps the OpenAI Agents SDK *and* LangGraph behind one event log, which is the actual
differentiator — but a comparison page that ignores them would be wrong, and a reader who knows
them and finds no mention will discount the page.

**3. The durable/human-approval space is crowded and well-funded.** Temporal, Restate, DBOS,
Pydantic AI, Agentspan, AgentScope Runtime, Bedrock AgentCore all rank for it. Competing on
"durable human approval" alone is competing with Temporal's content budget. The queries where
AgentDeck is genuinely unusual — combining two specific SDKs, one event log across both — are the
ones with no incumbent.

## The 30 questions

Re-run these verbatim; a changed question makes the comparison meaningless.

<details>
<summary>Full list</summary>

```
pause and resume an AI agent
resume an AI agent run from another process
durable human approval for an AI agent
human in the loop approval workflow python
approve an agent action before it runs
cancel a running LLM agent by id
event log for every agent run
stream agent events over SSE python
one ordered event log per agent run
share application state across agents and tools
pass typed context to an agent tool python
serve an AI agent over HTTP without writing a server
declarative agent framework python
wrap an existing LangGraph agent
use OpenAI Agents SDK with LangGraph
combine OpenAI Agents SDK and LangGraph in one app
production runtime for AI agents python
run AI agent workflows that survive a restart
checkpoint a LangGraph workflow for approvals
agent sessions across processes redis
connect MCP servers to a python agent
declare MCP tools per agent
agent skills SKILL.md python
run control pause resume cancel AI agents
what happens when an agent tool raises
observability for AI agent runs python
compose agents workflows skills in one project
python harness over the OpenAI Agents SDK
multi agent handoff python framework
agent framework that does not replace LangGraph
```

</details>

The eight run against web search this round: numbers 1, 3, 14, 15, 17, 12, 30, plus the branded
query `AgentDeck SDK python agents workflows skills`.

## How to re-run

Two channels are scripted and take about three minutes:

```bash
# Context7 — position in the top 10, or "-"
curl -sS --get --data-urlencode "query=$Q" https://context7.com/api/v1/search

# GitHub repositories — position in the top 10, or "-"
gh api -X GET search/repositories -f q="$Q" -f per_page=10 -q '[.items[].full_name]|join(" ")'
```

Web search is manual, and **ChatGPT Search and Perplexity have no API path from here** — they need
a person with a browser. Recording them as "not measured" rather than "not returned" matters: the
two are not the same, and a later reading that quietly conflates them would show a fake gain.

## What would count as movement

Not stars, and not traffic. The plan's goal is positional, so the measure is positional:

- **Context7 0 → any** on a problem-shaped query. The manifest now scopes and rules the index; the
  next scan is the first honest test of whether that changes retrieval or only content.
- **Web search: the branded query returning the project at all.** Until `AgentDeck SDK` finds
  AgentDeck, nothing downstream can work. This is an indexing question, not a content one, and it
  should resolve on its own within weeks — if it does not, something is wrong with the site.
- **`use your existing LangGraph agent`** — the one query where a single good page could plausibly
  rank, because nothing currently answers it.

Next reading: **2026-09-12.** Re-run before writing anything new, so the pages written between now
and then are what the delta measures.
