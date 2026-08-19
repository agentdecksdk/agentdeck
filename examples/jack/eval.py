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

from evalset import GOLDEN, Case
from jack.agent import jack
from jack.corpus import DocsCorpus
from jack.session import BoundedSessions

from agentdeck import Deck

GOLDEN_IDS = {case.id for case in GOLDEN}

_BACKTICKED = re.compile(r"`([^`\n]{2,60})`")
# Code-shaped: an env var, a dotted name, a call, a snake_case or CamelCase identifier. Prose in
# backticks ("the `name` argument") is not worth checking and would drown the signal.
_CODEISH = re.compile(r"^(?:[A-Z][A-Z0-9_]{3,}|[\w.]+\(\)?|[a-z]+_[a-z_]+|[A-Z][a-z]+[A-Z]\w*)$")
_REFUSAL = re.compile(
    r"do(?:es)? not (?:cover|mention|indicate|appear|contain|include|support|currently)"
    r"|no(?:t| ) (?:documented|covered|directly supported|currently (?:offer|support))"
    r"|could not find|couldn't find|no built-in|does not (?:offer|provide)",
    re.I,
)


def code_tokens(answer: str) -> set[str]:
    return {token.strip("()") for token in _BACKTICKED.findall(answer) if _CODEISH.match(token)}


async def ask(deck, corpus, case: Case, session: str | None) -> tuple[str, list[str], list[str]]:
    """One turn. Returns the answer, the slugs read, and every tool called."""
    answer, read, tools = "", [], []
    async for event in deck.stream("Jack", case.question, context=corpus, session_id=session):
        if event.kind == "tool.call.started":
            tools.append(event.payload.tool)
            if event.payload.tool == "read_doc":
                read.append(event.payload.args.get("slug", ""))
        elif event.kind == "text.delta":
            answer += event.payload.text
    return answer, read, tools


def judge(case: Case, answer: str, read: list[str], tools: list[str], everything: str) -> list[str]:
    """Why this case failed, or an empty list. Every check is exact; none is a score."""
    problems: list[str] = []
    tokens = code_tokens(answer)
    invented = sorted(token for token in tokens if token not in everything)
    if invented:
        problems.append(f"invented: {invented}")

    refused = bool(_REFUSAL.search(answer))
    lowered = answer.lower()
    missing = [phrase for phrase in case.must_mention if phrase.lower() not in lowered]

    if case.expect == "refuse":
        if not refused:
            problems.append("answered a question the corpus does not cover")
    elif case.expect == "changelog":
        if "read_changelog" not in tools:
            problems.append("a version question answered without reading the changelog")
        if refused:
            problems.append("refused a version question the changelog answers")
    else:
        if refused:
            problems.append("refused a question the corpus does answer")
        elif not read and not case.follows:
            problems.append("answered without reading a page")
    if missing:
        problems.append(f"missing: {missing}")
    return problems


async def main() -> int:
    corpus = DocsCorpus()
    # The changelog counts as grounding: the instructions send version questions to it, and a
    # name that appears only there is history, not invention.
    everything = "\n".join([*corpus.pages.values(), *(body for _v, _d, body in corpus.releases)])
    failures: list[str] = []
    chain = 0

    async with Deck(agents=[jack], context=DocsCorpus, session_factory=BoundedSessions()) as deck:
        for case in GOLDEN:
            if case.follows is None:
                chain += 1
            session = f"eval-{chain}" if case.category == "multi-turn" else None

            answer, read, tools = await ask(deck, corpus, case, session)
            problems = judge(case, answer, read, tools, everything)

            if problems:
                failures.append(case.id)
            print(f"{'FAIL' if problems else 'ok  '} {case.id:16} {case.category:15} tools={tools or '-'}")
            for problem in problems:
                print(f"       ! {problem}")

    passed = len(GOLDEN) - len(failures)
    print(f"\n{passed}/{len(GOLDEN)} valid")
    if failures:
        print(f"failed: {', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY is not set  -  this layer talks to a real model, unlike the gate")
    sys.exit(asyncio.run(main()))
