"""Jack  -  the assistant that answers questions about AgentDeck, built on AgentDeck.

Three tools over one context. None is wrapped in ``@function_tool``: a tool that declares a
``Context`` parameter must stay a plain function, because the decorator would put that parameter
into the schema the model sees. ``build()`` compiles it instead, and the context argument is
absent from what the model is offered  -  it only ever chooses ``query``, ``slug`` or ``subject``.
"""

from __future__ import annotations

from agentdeck import Agent, Context
from jack.corpus import DocsCorpus  # noqa: TC001  -  Context[DocsCorpus] is resolved at runtime


def search_docs(query: str, docs: Context[DocsCorpus]) -> str:
    """Find AgentDeck documentation pages matching a query. Returns slugs with a matching line
    from each; call read_doc on a slug to get the full page."""
    hits = docs.data.search(query)
    if not hits:
        return f"no page matches {query!r}"
    return "\n".join(f"{slug}: {excerpt}" for slug, excerpt in hits)


def read_doc(slug: str, docs: Context[DocsCorpus]) -> str:
    """Read one AgentDeck documentation page in full, by its slug (e.g. 'concepts/agents')."""
    page = docs.data.pages.get(slug)
    if page is None:
        return f"no page {slug!r}. Available pages:\n{docs.data.index()}"
    return page


def read_changelog(subject: str, docs: Context[DocsCorpus]) -> str:
    """Read AgentDeck's release history. Pass a version ('3.0.0', 'latest') for that release's
    notes, or a topic ('Context', 'AudioBlock') to find which releases changed it."""
    return docs.data.changelog(subject)


def instructions(docs: Context[DocsCorpus]) -> str:
    """The prompt, with the site's own page list folded in.

    A callable rather than a string so the page index is read from the corpus that is actually
    loaded, not copied into a constant that would rot the first time a page is renamed. The
    index is worth its ~700 bytes: with it the agent reads `concepts/skills` directly instead of
    searching for a page whose name it already knows, and search stays for the questions where
    nobody knows which page answers them.

    Only the return value reaches the model. The context itself never does.
    """
    return f"{INSTRUCTIONS}\nThe documentation pages, by slug:\n\n{docs.data.index()}\n"


INSTRUCTIONS = """\
You are Jack, the AgentDeck developer agent. You answer questions about AgentDeck, a
declarative harness over the OpenAI Agents SDK and LangGraph, and you are embedded in
AgentDeck's own documentation site.

Ground every answer in the documentation, not in what you remember about AgentDeck or about
other agent frameworks:

- Before naming any AgentDeck class, method, argument, environment variable or event kind, read
  a page that names it. `search_docs` to find the page, `read_doc` to read it.
- Never invent an API. If the documentation does not cover something, say exactly that and name
  the closest thing it does cover. A wrong API is worse than no answer, because the reader will
  try it.
- The documentation describes the **current** release. For anything about *versions*  -  what
  changed, when something arrived, whether an upgrade breaks you  -  use `read_changelog`. The
  documentation pages do not say when anything changed.
- `read_changelog` is history, and history contains APIs that were later removed. Never present
  something found only in the changelog as though it exists today: say which release it belongs
  to, and check the documentation before recommending it.
- Cite the pages you used, by slug, at the end of the answer.

The reader may be looking at a page when they ask. If the question mentions one, read that page
first  -  "this page" and "the code above" mean that page.

Keep answers short and concrete. Prefer a code example from the documentation over a paraphrase
of one. If the question has a one-line answer, give the one line.
"""

jack = Agent(
    name="Jack",
    instructions=instructions,
    tools=[search_docs, read_doc, read_changelog],
    # `max_tokens` is the only *structural* answer to "can someone use this to write their essay
    # instead of asking about the docs". The instruction above says to stay on topic, and an
    # instruction is persuadable; a token ceiling is not. A grounded docs answer with a code
    # example fits comfortably here, and nothing worth stealing a model for does.
    #
    # `temperature` low for the same reason it is low on any lookup: this agent's job is to
    # report what a page says, and creative variation in that is a defect.
    model_settings={"max_tokens": 900, "temperature": 0.1},
)

__all__ = ["INSTRUCTIONS", "instructions", "jack", "read_changelog", "read_doc", "search_docs"]
