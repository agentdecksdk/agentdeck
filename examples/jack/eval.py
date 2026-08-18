"""Layer B of the eval split: does the assistant stay grounded, against a real model?

    make eval-docs-agent

Layer A (`tests/test_jack_server.py`) checks retrieval offline and runs in the gate.
This layer needs a model, so it does not  -  a model-graded suite inside a required check is a
flaky gate, and #219 asks for CI "at least at build/integration-test level", which layer A meets.

**There is no judge here.** Grounding is asserted against the run's own event log, which makes it
exact rather than probabilistic:

- `tool.call.started` says whether the agent read anything before answering. An answer with no
  tool call came out of the model's memory, whatever it says.
- Every code-shaped token in the answer is checked against the corpus. A token that appears in
  **no page at all** is an invented API  -  that is a fact about two strings, not an opinion, and
  no LLM judge is better at it than `in` is. A token that is in the corpus but not in the pages
  this run read is reported separately: real, but recalled rather than looked up.

The defect that motivated this layer was found by hand and would have scored perfectly on every
faithfulness metric: the agent refused a question the docs answer in full, having read only a
weak search excerpt. That is a *false negative*, and judge metrics are built to catch false
positives. `read_something` is the check that catches it.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys

from jack.agent import jack
from jack.corpus import DocsCorpus

from agentdeck import Deck

# One per category #219 names, so a gap in coverage is visible as a missing row.
QUESTIONS = [
    ("agent creation", "how do I create an agent?"),
    ("deck composition", "what is Deck responsible for?"),
    ("tools", "how do I give an agent a tool?"),
    ("skills", "show me an example using skills"),
    ("workflows", "how do I define a workflow?"),
    ("runtime context", "how does runtime context work?"),
    ("events", "what is in the event log and how do I read a run back?"),
    ("observability", "how do I send traces to Langfuse?"),
    ("version-specific", "what is the default value of AGENTDECK_EVENTS?"),
    ("multimodal", "can I send an image to an agent?"),
    ("run control", "how do I pause and resume a run?"),
    ("refusal", "how do I add rate limiting to my deck?"),
]

REFUSAL_EXPECTED = {"refusal"}
"""Categories where the *right* answer is "the documentation does not cover this". Scored
inverted: naming an API here is the failure, because nothing in the corpus supports one."""

_BACKTICKED = re.compile(r"`([^`\n]{2,60})`")
# Code-shaped: an env var, a dotted name, a call, a snake_case or CamelCase identifier. Prose in
# backticks ("the `name` argument") is not worth checking and would drown the signal.
_CODEISH = re.compile(r"^(?:[A-Z][A-Z0-9_]{3,}|[\w.]+\(\)?|[a-z]+_[a-z_]+|[A-Z][a-z]+[A-Z]\w*)$")
_REFUSAL = re.compile(
    r"do(?:es)? not (?:cover|mention|indicate|appear)|no(?:t| ) (?:documented|covered)|could not find", re.I
)


def code_tokens(answer: str) -> set[str]:
    return {token.strip("()") for token in _BACKTICKED.findall(answer) if _CODEISH.match(token)}


async def main() -> int:
    corpus = DocsCorpus()
    everything = "\n".join(corpus.pages.values())
    failures = 0

    async with Deck(agents=[jack], context=DocsCorpus) as deck:
        for category, question in QUESTIONS:
            read: list[str] = []
            answer = ""
            async for event in deck.stream("Jack", question, context=corpus):
                if event.kind == "tool.call.started" and event.payload.tool == "read_doc":
                    read.append(event.payload.args.get("slug", ""))
                elif event.kind == "text.delta":
                    answer += event.payload.text

            tokens = code_tokens(answer)
            invented = sorted(token for token in tokens if token not in everything)
            grounded_text = "\n".join(corpus.pages[slug] for slug in read if slug in corpus.pages)
            recalled = sorted(token for token in tokens - set(invented) if token not in grounded_text)
            refused = bool(_REFUSAL.search(answer))

            problems = []
            if invented:
                problems.append(f"invented: {invented}")
            if category in REFUSAL_EXPECTED:
                if not refused:
                    problems.append("answered a question the docs do not cover")
            elif not read:
                problems.append("answered without reading a page")
            elif refused:
                problems.append("refused a question the docs do answer")

            failures += bool(problems)
            mark = "FAIL" if problems else "ok  "
            print(f"{mark} {category:18} read={read or '-'}")
            for problem in problems:
                print(f"       ! {problem}")
            if recalled:
                print(f"       ~ in the corpus but not in the pages read: {recalled}")

    print(f"\n{len(QUESTIONS) - failures}/{len(QUESTIONS)} grounded")
    return 1 if failures else 0


if __name__ == "__main__":
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY is not set  -  this layer talks to a real model, unlike the gate")
    sys.exit(asyncio.run(main()))
