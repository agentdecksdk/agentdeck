"""The checks that must not be judged, and the two that must.

`InventedAPI` and `CitationValid` are facts about strings. A judge would cost money to be less
certain, and on the second it would be actively wrong: whether a cited slug is a real page is
settled by a dict lookup, and a model asked the same question will occasionally say yes about a
page that does not exist. They subclass `BaseMetric` only so they land in the same report as the
judged ones. No model is loaded and `evaluation_cost` stays zero.

`answered` and `declined` are judged, and both are custom for a measured reason recorded below.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from deepeval.metrics import BaseMetric

if TYPE_CHECKING:
    from deepeval.test_case import LLMTestCase

_BACKTICKED = re.compile(r"`([^`\n]{2,60})`")
# Code-shaped: an env var, a dotted name, a call, snake_case or CamelCase. Prose in backticks
# ("the `name` argument") is not worth checking and would drown the signal.
_CODEISH = re.compile(r"^(?:[A-Z][A-Z0-9_]{3,}|[\w.]+\(\)?|[a-z]+_[a-z_]+|[A-Z][a-z]+[A-Z]\w*)$")
_CITED = re.compile(r"\b([a-z0-9-]+(?:/[a-z0-9-]+)+)\b")


def code_tokens(answer: str) -> set[str]:
    return {token.strip("()") for token in _BACKTICKED.findall(answer) if _CODEISH.match(token)}


class _Exact(BaseMetric):
    """Shared plumbing for a metric that runs no model."""

    def __init__(self, threshold: float = 1.0) -> None:
        self.threshold = threshold
        self.async_mode = False
        self.include_reason = True
        self.evaluation_cost = 0
        self.strict_mode = False
        self.verbose_mode = False

    async def a_measure(self, test_case: LLMTestCase, *args: Any, **kwargs: Any) -> float:
        return self.measure(test_case, *args, **kwargs)

    def is_successful(self) -> bool:
        return bool(self.success)


class InventedAPI(_Exact):
    """Fails when the answer names something that appears nowhere in the corpus.

    A wrong API is worse than no answer, because the reader will try it. `grounding` is every doc
    page plus every release body: the changelog is a source the instructions actively send version
    questions to, so a name found only there is history, not invention.
    """

    __name__ = "Invented API"

    def __init__(self, grounding: str, threshold: float = 1.0) -> None:
        super().__init__(threshold)
        self.grounding = grounding

    def measure(self, test_case: LLMTestCase, *_: Any, **__: Any) -> float:
        invented = sorted(t for t in code_tokens(test_case.actual_output or "") if t not in self.grounding)
        self.score = 0.0 if invented else 1.0
        self.success = not invented
        self.reason = f"names nothing in the corpus: {invented}" if invented else "every name appears in the corpus"
        return self.score


class CitationValid(_Exact):
    """Fails when a cited slug is not a real page, or was never opened on this run.

    Two different lies, and the second is the quieter one: citing a page that exists but was not
    read is how an answer from memory acquires the look of a sourced one.
    """

    __name__ = "Citation Valid"

    def __init__(self, slugs: set[str], threshold: float = 1.0) -> None:
        super().__init__(threshold)
        self.slugs = slugs

    def measure(self, test_case: LLMTestCase, *_: Any, **__: Any) -> float:
        cited = {c for c in _CITED.findall(test_case.actual_output or "") if "/" in c}
        real = {c for c in cited if c in self.slugs}
        unreal = sorted(cited - real)
        # The slugs opened on this run. `retrieval_context` holds page *bodies* for the judged
        # metrics, so the slugs travel in metadata rather than being parsed back out of prose.
        read = set((test_case.metadata or {}).get("read", []))
        unread = sorted(real - read) if read else []

        problems = []
        if unreal:
            problems.append(f"cited pages that do not exist: {unreal}")
        if unread:
            problems.append(f"cited pages it never opened: {unread}")

        self.score = 0.0 if problems else 1.0
        self.success = not problems
        self.reason = "; ".join(problems) if problems else "every citation is a real page it read"
        return self.score


def answered(judge: object, threshold: float = 0.7) -> Any:
    """Did the answer actually contain what was asked for?

    Custom rather than off the shelf, because the obvious candidate does not work.
    `AnswerRelevancyMetric` scores the shipped refusal bug **1.00**: "go and read the changelog and
    compare them yourself" is entirely *on topic*, and relevancy measures topicality. Measured, not
    assumed  -  see `docs/delivery/plan-jack-eval.md`.

    The distinction it has to make, and why the criteria are this explicit: "the documentation does
    not cover pricing" is a valid answer, while "you would need to look it up yourself" is the
    defect. Both decline. Only one is doing its job.
    """
    from deepeval.metrics import GEval
    from deepeval.test_case import LLMTestCaseParams

    return GEval(
        name="Answered",
        criteria=(
            "Decide whether the output actually gives the reader the information the input asked for. "
            "Score 0 if it declines, says it cannot help, explains a tool's limitations, or tells the "
            "reader to go and find or compare the information themselves. "
            "Score 0 if it only describes where the answer could be found instead of giving it. "
            "Score 10 only if the substantive answer is present in the output. "
            "Stating that the documentation genuinely does not cover a topic is a valid answer and scores 10."
        ),
        evaluation_params=[LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT],
        model=judge,
        threshold=threshold,
        async_mode=False,
    )


def declined(judge: object, threshold: float = 0.7) -> Any:
    """Did it correctly say the documentation does not cover this?

    The mirror of :func:`answered`, and a separate metric rather than a branch inside one, because
    the two ask different questions. Here the failure is confident invention; there it is a dodge.
    A metric covering both would have to know which case it was looking at, which is the runner's
    job.
    """
    from deepeval.metrics import GEval
    from deepeval.test_case import LLMTestCaseParams

    return GEval(
        name="Declined",
        criteria=(
            "The documentation does not cover the subject of the input. "
            "Score 10 if the output says so plainly, whether or not it also points at the nearest "
            "thing that is documented. "
            "Score 0 if the output supplies an API, a setting, a method or a procedure as though the "
            "documentation described one, or otherwise implies the capability exists."
        ),
        evaluation_params=[LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT],
        model=judge,
        threshold=threshold,
        async_mode=False,
    )
