"""The golden set: 50 things a reader actually asks Jack, and what a valid answer looks like.

Data only, so it can drive more than one runner. `eval.py` is the one that ships.

Each case carries a deterministic expectation rather than a score:

``expect``
    ``answer``    the corpus covers this; refusing is the failure.
    ``refuse``    the corpus does not cover this; naming an API is the failure.
    ``changelog`` a question about *versions*; answering without reading the changelog is the
                  failure, because the documentation pages never say when anything changed.

``must_mention``
    Strings a correct answer contains. Checked with ``in``, case-insensitively. Every one of
    these is a fact about the corpus, not a matter of taste, which is why no judge is needed to
    decide whether the answer was right.

``follows``
    The id of the case before it in the same conversation. Present only on the multi-turn cases,
    where the question is deliberately unresolvable on its own ("how do I pause it?") and the
    answer proves whether session memory carried.
"""

from __future__ import annotations

from typing import Literal, NamedTuple


class Case(NamedTuple):
    id: str
    category: str
    question: str
    expect: Literal["answer", "refuse", "changelog"]
    must_mention: tuple[str, ...] = ()
    follows: str | None = None


GOLDEN: tuple[Case, ...] = (
    # -- getting started ---------------------------------------------------------------
    Case("start-install", "getting started", "How do I install AgentDeck?", "answer", ("agentdeck-sdk",)),
    Case("start-first", "getting started", "How do I create my first agent?", "answer", ("Agent",)),
    Case("start-what", "getting started", "What is AgentDeck?", "answer"),
    Case(
        "start-model", "getting started", "How do I point AgentDeck at a different model?", "answer", ("OPENAI_MODEL",)
    ),
    # -- the primitives ----------------------------------------------------------------
    Case("prim-agent", "primitives", "What is an Agent in AgentDeck?", "answer", ("Agent",)),
    Case("prim-tool", "primitives", "How do I give an agent a tool?", "answer", ("tools",)),
    Case("prim-skill", "primitives", "What is a Skill and how do I add one?", "answer", ("SKILL.md",)),
    Case("prim-workflow", "primitives", "How do I define a workflow?", "answer", ("Workflow",)),
    Case("prim-deck", "primitives", "What is a Deck responsible for?", "answer", ("Deck",)),
    Case(
        "prim-context", "primitives", "How does Context work and why does the model not see it?", "answer", ("Context",)
    ),
    Case("prim-project", "primitives", "Where do my agents and workflows live on disk?", "answer", (".agentdeck",)),
    Case("prim-frompro", "primitives", "What does Deck.from_project() do?", "answer", ("from_project",)),
    Case("prim-build", "primitives", "What does build() validate?", "answer", ("build",)),
    # -- runs and execution ------------------------------------------------------------
    Case("run-start", "runs", "How do I start a run?", "answer", ("runs.start",)),
    Case(
        "run-states",
        "runs",
        "What are the run lifecycle states?",
        "answer",
        ("running", "paused", "waiting_answer", "completed", "failed", "cancelled"),
    ),
    Case("run-noqueued", "runs", "Is there a QUEUED state for runs?", "answer", ("running",)),
    Case("run-events", "runs", "How do I read a run's events?", "answer", ("events",)),
    Case("run-follow", "runs", "How do I stream events as a run happens?", "answer", ("follow",)),
    Case("run-rehydrate", "runs", "Can I pick up a run in another process?", "answer", ("runs.get",)),
    Case("run-session", "runs", "How do I keep conversation history across turns?", "answer", ("session_id",)),
    Case("run-busy", "runs", "What happens if I start a second run on a busy session?", "answer"),
    # -- control and human input ---------------------------------------------------------
    Case("ctl-pause", "control", "How do I pause and resume a run?", "answer", ("pause", "resume")),
    Case("ctl-cancel", "control", "How do I cancel a run?", "answer", ("cancel",)),
    Case("ctl-safe", "control", "When does a pause actually take effect?", "answer", ("safe point",)),
    Case("ctl-hitl", "control", "How do I make a workflow wait for human approval?", "answer", ("answer",)),
    Case("ctl-answer", "control", "How do I answer a run that is waiting for input?", "answer", ("answer",)),
    # -- events ---------------------------------------------------------------------------
    Case("ev-kinds", "events", "What event kinds does a run emit?", "answer", ("run.started", "run.completed")),
    Case("ev-tool", "events", "Which events tell me a tool was called?", "answer", ("tool.call.started",)),
    Case("ev-envelope", "events", "What fields does every event carry?", "answer", ("seq", "run_id")),
    Case(
        "ev-terminal",
        "events",
        "How do I know a run is finished from its events?",
        "answer",
        ("run.completed", "run.failed"),
    ),
    Case("ev-progress", "events", "How does a tool report progress while it works?", "answer", ("progress.reported",)),
    # -- operating it ----------------------------------------------------------------------
    Case("ops-events-cfg", "operations", "What is the default value of AGENTDECK_EVENTS?", "answer", ("memory://",)),
    Case("ops-durable", "operations", "How do I make my run log survive a restart?", "answer", ("sqlite",)),
    Case("ops-langfuse", "operations", "How do I send traces to Langfuse?", "answer", ("Langfuse",)),
    Case("ops-serve", "operations", "How do I expose a Deck over HTTP?", "answer", ("asgi",)),
    Case("ops-env", "operations", "What environment variables does AgentDeck read?", "answer", ("AGENTDECK_",)),
    Case("ops-cli", "operations", "What can the CLI do?", "answer", ("agentdeck",)),
    # -- integrations ------------------------------------------------------------------------
    Case("int-langgraph", "integrations", "Can I use an existing LangGraph graph?", "answer", ("LangGraph",)),
    Case(
        "int-oai", "integrations", "How does AgentDeck relate to the OpenAI Agents SDK?", "answer", ("OpenAI Agents",)
    ),
    Case("int-mcp", "integrations", "How do I attach an MCP server?", "answer", ("MCP",)),
    # -- versions: must reach for the changelog ------------------------------------------------
    Case("ver-latest", "versions", "What changed in the latest release?", "changelog"),
    Case("ver-diff", "versions", "What's the diff from the latest version?", "changelog"),
    Case("ver-breaking", "versions", "Was there a breaking change in 4.0.0?", "changelog"),
    Case("ver-when", "versions", "When did Context arrive?", "changelog"),
    # -- multi-turn: the question is unanswerable without the turn before it -------------------
    Case("mt-1-open", "multi-turn", "What is a Run?", "answer", ("Run",)),
    Case("mt-2-pause", "multi-turn", "How do I pause it?", "answer", ("pause",), follows="mt-1-open"),
    Case("mt-3-cross", "multi-turn", "Does that work across processes?", "answer", (), follows="mt-2-pause"),
    # -- the corpus genuinely does not cover these ----------------------------------------------
    Case("neg-pricing", "refusal", "What does AgentDeck Cloud cost per million tokens?", "refuse"),
    Case("neg-ratelimit", "refusal", "How do I add rate limiting to my deck?", "refuse"),
    Case("neg-auth", "refusal", "How do I enable multi-tenant authentication?", "refuse"),
    Case("neg-sandbox", "refusal", "How do I sandbox tool execution so a tool cannot touch the filesystem?", "refuse"),
    Case("neg-evalfw", "refusal", "Which built-in evaluation framework does AgentDeck ship?", "refuse"),
    Case("neg-market", "refusal", "How do I publish an agent to the AgentDeck marketplace?", "refuse"),
    # -- documented, but phrased the way a doubtful reader phrases it ----------------------------
    # The other direction of the same failure, and the direction that shipped: refusing these is
    # the bug. Each is answered in full by a page, and each is worded to invite a hedge.
    Case("hedge-prod", "hedged", "Can I actually run this in production, or is it a toy?", "answer"),
    Case("hedge-cancel", "hedged", "Is there any way to cancel a run that has already started?", "answer", ("cancel",)),
    Case("hedge-after", "hedged", "Once a run has finished, can I still get its events back?", "answer", ("events",)),
    Case("hedge-restart", "hedged", "Does anything at all survive if my process restarts?", "answer"),
    Case("hedge-human", "hedged", "Is it even possible to make a workflow wait for a person?", "answer"),
    Case(
        "hedge-isolate",
        "hedged",
        "Can I keep one tenant's runs from showing up in another's?",
        "answer",
        ("namespace",),
    ),
)

assert len({case.id for case in GOLDEN}) == len(GOLDEN), "case ids must be unique"
assert all(case.follows is None or case.follows in {c.id for c in GOLDEN} for case in GOLDEN)
