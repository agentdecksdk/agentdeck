"""The judged eval suite.

    make eval-jack                     # everything
    make eval-jack ARGS="refusal"      # one category

Runs every golden through Jack, then scores each answer in one report covering both kinds of
check: the two exact ones that must never be judged, and the judged ones that catch what exact
matching cannot.

The judged metric that matters most is `Answered`, and it is custom for a measured reason.
`AnswerRelevancyMetric` scores the shipped refusal bug 1.00, because a dodge naming the right
subject is still on topic. Relevancy is topicality. `Answered` asks the question that actually
separates a working assistant from a polite one.

Thresholds start permissive and ratchet. A suite that fails on arrival teaches people to skip it.
"""

from __future__ import annotations

import asyncio
import os
import sys

from deepeval import evaluate
from deepeval.metrics import FaithfulnessMetric
from deepeval.test_case import LLMTestCase

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evalset import GOLDEN, Case
from jack.agent import jack
from jack.corpus import DocsCorpus
from jack.session import BoundedSessions

from agentdeck import Deck
from evals.judge import Judge
from evals.metrics import CitationValid, InventedAPI, answered, declined

# Permissive on purpose; the plan ratchets these. The exact metrics sit at 1.0, the only ones
# allowed to be absolute because they cannot disagree with themselves between runs.
ANSWERED, FAITHFULNESS = 0.7, 0.8


def selected() -> list[Case]:
    """`python -m evals.run refusal hedged` runs those categories only.

    The endpoint rate-limits on input tokens per minute and the whole set is a few hundred judged
    calls, so running it in pieces is the normal way to use this, not a workaround.
    """
    wanted = {arg for arg in sys.argv[1:] if not arg.startswith("-")}
    if not wanted:
        return list(GOLDEN)
    chosen = [case for case in GOLDEN if case.category in wanted or case.id in wanted]
    if not chosen:
        sys.exit(f"nothing matches {sorted(wanted)}. Categories: {sorted({c.category for c in GOLDEN})}")
    return chosen


async def transcript(deck: Deck, corpus: DocsCorpus, golden: list[Case]) -> dict[str, tuple[str, list[str]]]:
    """Ask every golden once. Returns the answer and the pages read, per case id."""
    out: dict[str, tuple[str, list[str]]] = {}
    chain = 0
    for case in golden:
        if case.follows is None:
            chain += 1
        session = f"eval-{chain}" if case.category == "multi-turn" else None
        answer, read, failure = "", [], ""
        try:
            async for event in deck.stream("Jack", case.question, context=corpus, session_id=session):
                if event.kind == "tool.call.started" and event.payload.tool == "read_doc":
                    read.append(event.payload.args.get("slug", ""))
                elif event.kind == "text.delta":
                    answer += event.payload.text
                elif event.kind == "run.failed":
                    failure = f"{event.payload.error_code}: {event.payload.message}"
        except Exception as exc:  # noqa: BLE001  -  a dead run is a result, not a crash
            failure = f"{type(exc).__name__}: {exc}"

        # An empty answer is a finding, not a reason to abort the suite. It is also what a dead
        # session looks like from outside, which is the failure worth never missing.
        if not answer.strip():
            answer = f"[no answer produced] {failure}".strip()
        out[case.id] = (answer, read)
        print(f"  asked {case.id}{'  FAILED: ' + failure if failure else ''}", flush=True)
    return out


def as_test_case(case: Case, answer: str, read: list[str], corpus: DocsCorpus) -> LLMTestCase:
    return LLMTestCase(
        name=case.id,
        input=case.question,
        actual_output=answer,
        # The pages he opened, not the whole corpus: faithfulness should ask whether the answer
        # follows from what he actually read.
        retrieval_context=[corpus.pages[slug] for slug in read if slug in corpus.pages] or [""],
        metadata={"read": read, "expect": case.expect},
    )


async def main() -> int:
    corpus = DocsCorpus()
    golden = selected()
    grounding = "\n".join([*corpus.pages.values(), *(body for _v, _d, body in corpus.releases)])

    async with Deck(agents=[jack], context=DocsCorpus, session_factory=BoundedSessions()) as deck:
        answers = await transcript(deck, corpus, golden)

    judge = Judge()
    exact = [InventedAPI(grounding), CitationValid(set(corpus.pages))]

    # Two groups, two questions. "Did you answer?" and "did you correctly say you cannot?" are
    # different criteria, and one metric covering both would have to know which case it was
    # looking at. Grouping is where that knowledge belongs.
    groups = {
        "expected an answer": (
            [c for c in golden if c.expect != "refuse"],
            [
                answered(judge, threshold=ANSWERED),
                FaithfulnessMetric(threshold=FAITHFULNESS, model=judge, async_mode=False),
                *exact,
            ],
        ),
        "expected a refusal": (
            [c for c in golden if c.expect == "refuse"],
            [declined(judge, threshold=ANSWERED), *exact],
        ),
    }

    failures: list[str] = []
    total = 0
    for label, (in_group, metrics) in groups.items():
        if not in_group:
            continue
        print(f"\n--- {label}: {len(in_group)} cases")
        total += len(in_group)
        result = evaluate(
            test_cases=[as_test_case(c, *answers[c.id], corpus) for c in in_group],
            metrics=metrics,
        )
        for test in getattr(result, "test_results", []):
            bad = [m.name for m in test.metrics_data or [] if not m.success]
            if bad:
                failures.append(f"  {test.name}: {', '.join(bad)}")

    print(f"\n{total - len(failures)}/{total} passed every metric")
    print("\n".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY is not set  -  this suite talks to a real model, unlike the gate")
    sys.exit(asyncio.run(main()))
