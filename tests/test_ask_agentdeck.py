"""``examples/ask-agentdeck/`` — the reference application (#219).

Two things are checked here, and neither reaches a model. **Composition**: the deck builds, the
declared context type is enforced, and the context parameter stays out of the schema the model
sees — the property the whole design rests on, and the one that fails silently if it breaks.
**Retrieval**: for a representative question, the page that answers it is near the top. That is
layer A of the plan's eval split (`docs/delivery/plan-219-delivery.md` ruling 4) — deterministic,
offline, and the layer that actually rots, because renaming a page changes what search returns
while every import still resolves.

The retrieval cases are deliberately asserted as *top-3, not top-1*: the agent is also given the
full page index in its instructions, so being second to a plausible neighbour is not a failure.
A page falling out of the top three means search stopped finding it at all.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agentdeck import Deck
from agentdeck.authoring.tools import compile_tool
from agentdeck.errors import ContextTypeError

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "ask-agentdeck"
sys.path.insert(0, str(EXAMPLE))

from ask_agentdeck.agent import ask, read_doc, search_docs  # noqa: E402 — needs the path above
from ask_agentdeck.corpus import DEFAULT_CONTENT_ROOT, DocsCorpus  # noqa: E402


@pytest.fixture(scope="module")
def corpus() -> DocsCorpus:
    return DocsCorpus()


def test_the_corpus_finds_every_published_page(corpus: DocsCorpus) -> None:
    assert len(corpus.pages) == len(list(DEFAULT_CONTENT_ROOT.rglob("*.mdx")))


def test_slugs_are_the_sites_own_slugs(corpus: DocsCorpus) -> None:
    """A slug here has to be a slug there: the docs panel sends the page the reader is on as a
    URL path, and `read_doc` is asked for pages by the name the site links them under. This is
    the same resolution `tests/test_docs_site.py` uses to check internal links.
    """
    for slug in corpus.pages:
        page = DEFAULT_CONTENT_ROOT / f"{slug}.mdx"
        assert page.is_file() or (DEFAULT_CONTENT_ROOT / slug / "index.mdx").is_file(), f"{slug} names no page"


# One case per question shape the docs panel will actually meet. Grown to #219's ten categories
# in the eval slice; this is the regression floor, not the benchmark.
RETRIEVAL_CASES = [
    ("how do I create an agent?", "concepts/agents"),
    ("how do tools work?", "guides/add-a-tool"),
    ("how do I define a workflow?", "concepts/workflows"),
    ("what is Deck responsible for?", "reference/deck"),
    ("show me an example using skills", "concepts/skills"),
    ("how does runtime context work?", "reference/deck"),
    ("can I send an image to an agent?", "reference/deck"),
    ("how do I pause a run?", "operating/pause-resume-cancel"),
    ("which store backend should I use?", "concepts/choosing-a-store-backend"),
    ("how do I serve a deck over HTTP?", "guides/serve-over-http"),
    ("what environment variables are there?", "reference/settings"),
    ("how do I wait for a human to approve something?", "guides/human-approval"),
]


@pytest.mark.parametrize("question,expected", RETRIEVAL_CASES, ids=[q for q, _ in RETRIEVAL_CASES])
def test_search_surfaces_the_page_that_answers_the_question(corpus: DocsCorpus, question: str, expected: str) -> None:
    ranked = [slug for slug, _excerpt in corpus.search(question)]
    assert expected in ranked[:3], f"{expected} not in top 3 for {question!r}: {ranked}"


def test_search_returns_nothing_rather_than_anything(corpus: DocsCorpus) -> None:
    """A query matching no page must come back empty. Returning the least-bad page instead would
    hand the agent a source to ground an answer in that has nothing to do with the question."""
    assert corpus.search("zzzznotaword") == []


def test_an_unknown_slug_answers_with_the_page_list(corpus: DocsCorpus) -> None:
    """A wrong guess should teach the agent the right slug in the same turn, not raise."""
    result = read_doc("concepts/nonexistent", _AsContext(corpus))
    assert "no page" in result
    assert "concepts/agents" in result


class _AsContext:
    """Stands in for the ``Context`` the runtime injects — the tools only ever read ``.data``."""

    def __init__(self, data: object) -> None:
        self.data = data


def test_the_context_parameter_is_absent_from_the_schema_the_model_sees() -> None:
    """The load-bearing property. If a context parameter leaked into the schema, the model would
    be asked to invent a `DocsCorpus`, and the failure would look like a confused model rather
    than a broken tool.
    """
    for tool in (search_docs, read_doc):
        compiled = compile_tool(tool, context_type=DocsCorpus)
        assert "docs" not in compiled.params_json_schema["properties"], compiled.params_json_schema


def test_the_deck_builds_with_the_corpus_as_its_declared_context() -> None:
    deck = Deck(agents=[ask], context=DocsCorpus).build()
    assert sorted(deck.agents) == ["AskAgentDeck"]


def test_a_deck_declaring_the_wrong_context_type_refuses_to_build() -> None:
    """`Deck(context=T)` earns its keep here: both tools and the instructions callable want a
    `DocsCorpus`, and declaring anything else is caught before a question is ever asked.
    """

    class Warehouse:
        """Some other application's environment."""

    with pytest.raises(ContextTypeError) as raised:
        Deck(agents=[ask], context=Warehouse).build()
    assert "DocsCorpus" in str(raised.value)
