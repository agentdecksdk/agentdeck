"""The documentation, in memory.

Every published page is an ``.mdx`` file on disk and the whole set is about 120 KB, so this is a
dict — read once at startup, keyed by the slug the site serves the page under. No index to
build, nothing to invalidate, and a page renamed on the site is a page renamed here.

That size is the whole justification, and it is written down rather than assumed: at this scale
a substring scan beats an embedding round-trip and cannot return a stale chunk. If the corpus
outgrows it, :meth:`DocsCorpus.search` gets a different body — its signature, and the tool built
on it, do not change.
"""

from __future__ import annotations

import re
from functools import cache
from math import log
from pathlib import Path

# examples/ask-agentdeck/ask_agentdeck/corpus.py -> the repo root
_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONTENT_ROOT = _REPO_ROOT / "docs-site" / "content"

_TITLE = re.compile(r"^title:\s*(.+)$", re.MULTILINE)
_WORD = re.compile(r"[a-z0-9_]+")
_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)


class DocsCorpus:
    """Every AgentDeck documentation page, addressed by its site slug.

    This is the application object the agent's tools are handed — a live thing the server owns,
    which is what makes it a context rather than an argument. The model never sees it.
    """

    def __init__(self, content_root: Path | None = None) -> None:
        self.root = content_root or DEFAULT_CONTENT_ROOT
        if not self.root.is_dir():
            raise FileNotFoundError(f"docs content root not found: {self.root}")
        self.pages: dict[str, str] = {_slug_of(path, self.root): path.read_text() for path in self.root.rglob("*.mdx")}
        if not self.pages:
            raise FileNotFoundError(f"no .mdx pages under {self.root}")

    def title_of(self, slug: str) -> str:
        found = _TITLE.search(self.pages[slug])
        return found.group(1).strip() if found else slug

    def index(self) -> str:
        """Every slug and title, one per line — small enough to sit in the instructions, which is
        what lets the agent pick a page to read instead of searching for one it already knows."""
        return "\n".join(f"{slug} — {self.title_of(slug)}" for slug in sorted(self.pages))

    def search(self, query: str) -> list[tuple[str, str]]:
        """``(slug, excerpt)`` for the pages best matching ``query``, best first.

        TF-IDF over whole-word matches, plus a bonus for the slug and title. A word on every page
        ("run", "agent") is worth almost nothing; a word on two ("image", "langfuse") decides the
        ranking. Plain counting does not survive real questions — *"can I send an image"* put run
        control first, because only "image" meant anything and the rest was everywhere.

        Deliberately not fuzzy: a miss is visible in the tool result and the agent can search
        again, whereas a fuzzy hit that confidently ranks the wrong page is invisible.
        """
        patterns = {word: _matcher(word) for word in _WORD.findall(query.lower()) if _stem(word) not in _STOPWORDS}
        if not patterns:
            return []
        lowered = {slug: text.lower() for slug, text in self.pages.items()}
        headings = {slug: f"{slug} {self.title_of(slug)}".lower() for slug in lowered}
        counts = {
            word: {slug: len(pattern.findall(text)) for slug, text in lowered.items()}
            for word, pattern in patterns.items()
        }
        # A word in no page has no document frequency to divide by, and scores nothing anyway.
        found = {word: per_page for word, per_page in counts.items() if any(per_page.values())}
        if not found:
            return []
        # Textbook inverse document frequency. `1 / df` was the first thing tried and does not
        # discriminate steeply enough: on "show me an example using skills" the three filler
        # words together outweighed `skills`, and the skills page came fourth.
        total = len(lowered)
        weights = {word: log(1 + total / sum(1 for n in per_page.values() if n)) for word, per_page in found.items()}
        scored = [
            (
                # Sublinear in the count: a page that discusses a term beats one that mentions
                # it, without a long page beating a focused one on length alone. Binary presence
                # left almost every page tied and sorting alphabetically.
                sum(weights[word] * (1 + log(n)) for word, per_page in found.items() if (n := per_page[slug]))
                # Triple for the slug or title — the one place an author said what a page is
                # about. "how do skills work" otherwise ranked concepts/skills third.
                + sum(3 * weights[word] for word in found if patterns[word].search(headings[slug])),
                slug,
            )
            for slug in lowered
            if any(per_page[slug] for per_page in found.values())
        ]
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        return [(slug, self._excerpt(slug, weights)) for _score, slug in scored[:5]]

    def _excerpt(self, slug: str, weights: dict[str, float]) -> str:
        """Lines showing *why* the page matched — rarest query word first, frontmatter skipped.

        This is the most load-bearing thirty lines in the app, because the excerpt is what
        decides whether the agent bothers to read the page. Returning the first line matching
        *any* query word produced the worst failure this has had: *"can I send an image to an
        agent"* correctly ranked `reference/deck` first, and then handed back that page's
        frontmatter ``description:`` line, which mentions no image. The agent read the excerpt,
        concluded the documentation did not cover images, and refused a question the page
        answers in full. A confident wrong refusal is worse than the invented API the grounding
        rule exists to prevent, because it looks like diligence.

        So: skip the frontmatter, and lead with the rarest word — ``image`` is what the question
        was about, ``send`` and ``agent`` are on every page.
        """
        body = _FRONTMATTER.sub("", self.pages[slug], count=1)
        lines = [stripped for line in body.splitlines() if len(stripped := line.strip()) > 20]
        picked: list[str] = []
        for word in sorted(weights, key=lambda w: -weights[w]):
            pattern = _matcher(word)
            hit = next((line for line in lines if pattern.search(line.lower()) and line not in picked), None)
            if hit is not None:
                picked.append(hit)
            if len(picked) == 2:
                break
        return " … ".join(line[:160] for line in picked) or self.title_of(slug)


# Words that appear on nearly every page, so scoring on them ranks by page length instead of by
# relevance. Not a general stopword list — just the ones this corpus is saturated with.
_STOPWORDS = frozenset(
    {"a", "an", "the", "is", "are", "do", "does", "how", "what", "i", "in", "of", "to", "and", "or", "it", "agentdeck"}
)


def _stem(word: str) -> str:
    """Drop a trailing plural ``s``, so the stopword list and the matcher both see one form.

    # ponytail: a suffix strip, not a stemmer. Real morphology (``queries`` -> ``query``) needs a
    # stemming dependency; add one when a miss traces to a word this cannot fold.
    """
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


@cache
def _matcher(word: str) -> re.Pattern[str]:
    """``\\bword s?\\b`` — whole words, either number.

    Word boundaries rather than ``in``, because substring matching quietly ranks by coincidence:
    *"how do tools work"* put the workflows page first, since ``work`` is inside ``workflow`` and
    the page has it in the slug *and* the title. Optional trailing ``s`` so the singular and
    plural find each other without needing the query and the page to agree on number.
    """
    return re.compile(rf"\b{re.escape(_stem(word))}s?\b")


def _slug_of(path: Path, root: Path) -> str:
    """``concepts/agents.mdx`` -> ``concepts/agents``; ``concepts/index.mdx`` -> ``concepts``.

    The site's own slug, not an invented id: the docs panel sends the page the reader is on as a
    URL path, and it has to arrive as something this dict can be keyed by.
    """
    relative = path.relative_to(root).with_suffix("")
    if relative.name != "index":
        return str(relative)
    return "index" if relative.parent == Path() else str(relative.parent)


__all__ = ["DEFAULT_CONTENT_ROOT", "DocsCorpus"]
