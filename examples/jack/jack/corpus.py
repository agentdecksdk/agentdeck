"""The documentation, in memory.

Every published page is an ``.mdx`` file on disk and the whole set is about 120 KB, so this is a
dict  -  read once at startup, keyed by the slug the site serves the page under. No index to
build, nothing to invalidate, and a page renamed on the site is a page renamed here.

That size is the whole justification, and it is written down rather than assumed: at this scale
a substring scan beats an embedding round-trip and cannot return a stale chunk. If the corpus
outgrows it, :meth:`DocsCorpus.search` gets a different body  -  its signature, and the tool built
on it, do not change.
"""

from __future__ import annotations

import re
from functools import cache
from math import log
from pathlib import Path

# examples/jack/jack/corpus.py -> the repo root
_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONTENT_ROOT = _REPO_ROOT / "docs-site" / "content"

CHANGELOG_SOURCE = _REPO_ROOT / "CHANGELOG.md"
"""Read from the repository, not from the site's `changelog.mdx`. That page reproduces only the
current release in full, and *"when did this change?"* needs every release."""

EXCLUDED = frozenset({"resources/changelog"})
"""Pages kept out of general search  -  not out of the assistant's reach.

The changelog is **history**, and history is where removed APIs live: it names
``agentdeck.adapters.caps`` and ``openai_agents.structured_output`` precisely because it is
recording that they were deleted. An answer grounded in it would be a *correct quotation of a
real page* describing something that no longer exists  -  the grounding rule defeated by obeying it.

It is also 450 lines mentioning every environment variable the project has ever had, which is how
this was noticed: publishing it pushed ``reference/settings`` out of the top three for *"what
environment variables are there?"*, and the retrieval test failed.

So it moves **behind its own tool** rather than out of the corpus. *"When was `Context` added?"*
is a real question whose answer is only here; it just must never arrive as a documentation
lookup. Roadmap and known-issues stay in general search  -  both describe the present.
"""

_TITLE = re.compile(r"^title:\s*(.+)$", re.MULTILINE)
_WORD = re.compile(r"[a-z0-9_]+")
_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
_RELEASE = re.compile(r"^## \[([^\]]+)\](?: - (\S+))?\s*$", re.MULTILINE)
_VERSIONISH = re.compile(r"^v?\d+\.\d+")


class DocsCorpus:
    """Every AgentDeck documentation page, addressed by its site slug.

    This is the application object the agent's tools are handed  -  a live thing the server owns,
    which is what makes it a context rather than an argument. The model never sees it.
    """

    def __init__(self, content_root: Path | None = None) -> None:
        self.root = content_root or DEFAULT_CONTENT_ROOT
        if not self.root.is_dir():
            raise FileNotFoundError(f"docs content root not found: {self.root}")
        found = {_slug_of(path, self.root): path.read_text() for path in self.root.rglob("*.mdx")}
        self.pages: dict[str, str] = {slug: text for slug, text in found.items() if slug not in EXCLUDED}
        if not self.pages:
            raise FileNotFoundError(f"no .mdx pages under {self.root}")
        self.releases: list[tuple[str, str, str]] = _releases()

    def changelog(self, subject: str) -> str:
        """One release's notes, or the releases that mention a topic.

        Both from one parameter, because they are one question asked two ways and two tools would
        make the model choose between them. A version-shaped subject (``3.0.0``, ``v3.0.0``,
        ``latest``) reads as *"what changed in that release"*; anything else reads as *"when did
        this change"* and searches every release for it.
        """
        wanted = subject.strip().lstrip("v").lower()
        if not wanted or wanted == "latest":
            version, date, body = next((r for r in self.releases if r[0].lower() != "unreleased"), self.releases[0])
            return f"## {version}{f'  -  {date}' if date else ''}\n\n{body.strip()}"
        for version, date, body in self.releases:
            if version.lower() == wanted:
                return f"## {version}{f'  -  {date}' if date else ''}\n\n{body.strip()}"
        if _VERSIONISH.match(wanted):
            return f"no release {subject!r}. Releases: {', '.join(v for v, _d, _b in self.releases)}"
        return self._mentions(subject)

    def _mentions(self, topic: str) -> str:
        """Every release whose notes mention ``topic``, newest first, with the lines that matched.

        Answers what a version lookup cannot: *when* did this appear, change, or go away. The
        release heading is on every hit, because a changelog line without its version is worse
        than no line  -  it reads as current.
        """
        # Lowercased first: `_matcher` escapes the word as given and every body is compared
        # lowercase, so an un-lowered `Context` would match nothing at all.
        words = [word for word in _WORD.findall(topic.lower()) if _stem(word) not in _STOPWORDS]
        if not words:
            return "ask about a version (3.0.0, latest) or a topic (Context, AudioBlock)"
        pattern = _matcher(words[0])
        hits: list[str] = []
        for version, date, body in self.releases:
            lines = [
                stripped
                for line in body.splitlines()
                if len(stripped := line.strip()) > 20 and pattern.search(stripped.lower())
            ]
            if lines:
                head = f"### {version}{f' ({date})' if date else ''}"
                hits.append(head + "\n" + "\n".join(f"- {line[:200]}" for line in lines[:3]))
        if not hits:
            return f"no release mentions {topic!r}"
        return f"Releases mentioning {topic!r}, newest first:\n\n" + "\n\n".join(hits[:5])

    def title_of(self, slug: str) -> str:
        found = _TITLE.search(self.pages[slug])
        return found.group(1).strip() if found else slug

    def index(self) -> str:
        """Every slug and title, one per line  -  small enough to sit in the instructions, which is
        what lets the agent pick a page to read instead of searching for one it already knows."""
        return "\n".join(f"{slug}  -  {self.title_of(slug)}" for slug in sorted(self.pages))

    def search(self, query: str) -> list[tuple[str, str]]:
        """``(slug, excerpt)`` for the pages best matching ``query``, best first.

        TF-IDF over whole-word matches, plus a bonus for the slug and title. A word on every page
        ("run", "agent") is worth almost nothing; a word on two ("image", "langfuse") decides the
        ranking. Plain counting does not survive real questions  -  *"can I send an image"* put run
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
                # Triple for the slug or title  -  the one place an author said what a page is
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
        """Lines showing *why* the page matched  -  rarest query word first, frontmatter skipped.

        This is the most load-bearing thirty lines in the app, because the excerpt is what
        decides whether the agent bothers to read the page. Returning the first line matching
        *any* query word produced the worst failure this has had: *"can I send an image to an
        agent"* correctly ranked `reference/deck` first, and then handed back that page's
        frontmatter ``description:`` line, which mentions no image. The agent read the excerpt,
        concluded the documentation did not cover images, and refused a question the page
        answers in full. A confident wrong refusal is worse than the invented API the grounding
        rule exists to prevent, because it looks like diligence.

        So: skip the frontmatter, and lead with the rarest word  -  ``image`` is what the question
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
# relevance. Not a general stopword list  -  just the ones this corpus is saturated with.
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
    """``\\bword s?\\b``  -  whole words, either number.

    Word boundaries rather than ``in``, because substring matching quietly ranks by coincidence:
    *"how do tools work"* put the workflows page first, since ``work`` is inside ``workflow`` and
    the page has it in the slug *and* the title. Optional trailing ``s`` so the singular and
    plural find each other without needing the query and the page to agree on number.
    """
    return re.compile(rf"\b{re.escape(_stem(word))}s?\b")


def _releases() -> list[tuple[str, str, str]]:
    """``(version, date, body)`` per release heading in ``CHANGELOG.md``, newest first  -  the
    file's own order, since Keep a Changelog puts the newest at the top."""
    text = CHANGELOG_SOURCE.read_text()
    heads = list(_RELEASE.finditer(text))
    return [
        (m.group(1), m.group(2) or "", text[m.end() : (heads[i + 1].start() if i + 1 < len(heads) else len(text))])
        for i, m in enumerate(heads)
    ]


def _slug_of(path: Path, root: Path) -> str:
    """``concepts/agents.mdx`` -> ``concepts/agents``; ``concepts/index.mdx`` -> ``concepts``.

    The site's own slug, not an invented id: the docs panel sends the page the reader is on as a
    URL path, and it has to arrive as something this dict can be keyed by.
    """
    relative = path.relative_to(root).with_suffix("")
    if relative.name != "index":
        return str(relative)
    return "index" if relative.parent == Path() else str(relative.parent)


__all__ = ["CHANGELOG_SOURCE", "DEFAULT_CONTENT_ROOT", "EXCLUDED", "DocsCorpus"]
