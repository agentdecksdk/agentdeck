"""``examples/jack/``  -  the reference application (#219).

Two things are checked here, and neither reaches a model. **Composition**: the deck builds, the
declared context type is enforced, and the context parameter stays out of the schema the model
sees  -  the property the whole design rests on, and the one that fails silently if it breaks.
**Retrieval**: for a representative question, the page that answers it is near the top. That is
layer A of the plan's eval split (`docs/delivery/plan-219-delivery.md` ruling 4)  -  deterministic,
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

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "jack"
sys.path.insert(0, str(EXAMPLE))

from jack.agent import jack, read_changelog, read_doc, search_docs  # noqa: E402  -  needs the path above
from jack.corpus import DEFAULT_CONTENT_ROOT, EXCLUDED, DocsCorpus  # noqa: E402


@pytest.fixture(scope="module")
def corpus() -> DocsCorpus:
    return DocsCorpus()


def test_the_corpus_finds_every_published_page_except_the_excluded(corpus: DocsCorpus) -> None:
    assert len(corpus.pages) == len(list(DEFAULT_CONTENT_ROOT.rglob("*.mdx"))) - len(EXCLUDED)


def test_history_never_grounds_a_documentation_answer(corpus: DocsCorpus) -> None:
    """The changelog names removed APIs by design, so an answer grounded in it would be a correct
    quotation of a real page describing something that no longer exists. It stays reachable
    through `read_changelog`, which is a different question with a different tool.
    """
    assert "resources/changelog" not in corpus.pages
    assert (DEFAULT_CONTENT_ROOT / "resources" / "changelog.mdx").is_file(), "excluded from search, not unpublished"


def test_the_changelog_tool_answers_by_version_and_by_topic(corpus: DocsCorpus) -> None:
    """One parameter, two questions  -  they are one question asked two ways, and two tools would
    make the model choose between them."""
    # The version this tree is, not a literal: "latest" moves every release, and a hardcoded one
    # turns a correct answer into a red gate  -  which is what it did on the v3.0.2 rename.
    import tomllib
    from pathlib import Path

    current = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())["project"]["version"]
    assert current in read_changelog("latest", _AsContext(corpus))

    topic = read_changelog("AudioBlock", _AsContext(corpus))
    assert "AudioBlock" in topic
    assert "3.0.0" in topic, "a changelog line without its release reads as current"

    unknown = read_changelog("9.9.9", _AsContext(corpus))
    assert "no release" in unknown and "3.0.0" in unknown, "an unknown version lists the real ones"
    assert "no release mentions" in read_changelog("zzzznotaword", _AsContext(corpus))


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
    ("how do I create an agent?", "build-your-deck/agents"),
    ("how do tools work?", "build-your-deck/tools"),
    ("how do I define a workflow?", "build-your-deck/workflows"),
    ("what is Deck responsible for?", "build-your-deck/deck"),
    ("show me an example using skills", "build-your-deck/skills"),
    ("how does runtime context work?", "build-your-deck/context"),
    ("can I send an image to an agent?", "reference/deck"),
    ("how do I pause a run?", "runs-and-control/pause-resume"),
    ("which store backend should I use?", "reference/settings"),
    ("how do I serve a deck over HTTP?", "reference/deck"),
    ("what environment variables are there?", "reference/settings"),
    ("how do I wait for a human to approve something?", "runs-and-control/human-input"),
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
    result = read_doc("build-your-deck/nonexistent", _AsContext(corpus))
    assert "no page" in result
    assert "build-your-deck/agents" in result


class _AsContext:
    """Stands in for the ``Context`` the runtime injects  -  the tools only ever read ``.data``."""

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
    deck = Deck(agents=[jack], context=DocsCorpus).build()
    assert sorted(deck.agents) == ["Jack"]


def test_a_deck_declaring_the_wrong_context_type_refuses_to_build() -> None:
    """`Deck(context=T)` earns its keep here: both tools and the instructions callable want a
    `DocsCorpus`, and declaring anything else is caught before a question is ever asked.
    """

    class Warehouse:
        """Some other application's environment."""

    with pytest.raises(ContextTypeError) as raised:
        Deck(agents=[jack], context=Warehouse).build()
    assert "DocsCorpus" in str(raised.value)
