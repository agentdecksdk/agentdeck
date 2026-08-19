# Plan: Jack's evaluation suite

**Baseline:** 40 offline tests in the gate, 50 goldens in `examples/jack/evalset.py`, one custom runner.
**Proposal:** keep the deterministic layer, adopt DeepEval for everything it does better, and cover
the seven aspects of Jack that nothing currently measures.
**Status:** plan. Nothing here is built.

## What Jack is, for evaluation purposes

A RAG agent with three tools over a 32-page corpus, served unauthenticated over HTTP, holding
multi-turn conversations with strangers. That is four different systems to evaluate and today only
the first two are measured at all.

| Aspect | Measured today | By what |
|---|---|---|
| Retrieval finds the right page | partly | `search_docs` unit tests, offline |
| The answer is grounded | yes | exact token check against the corpus |
| The right tools were called | barely | one check: version questions must read the changelog |
| The answer is *useful* | **no** | nothing. This is where the refusal bug lives |
| Multi-turn memory holds | barely | 3 goldens, no metric |
| He stays in role under attack | **no** | one manual stress test, not repeatable |
| Citations are real | **no** | nothing |

## Why DeepEval, and where it does not win

The existing runner's argument against LLM judges is half right, and the half that is right must
survive the migration.

| Check | Best tool | Why |
|---|---|---|
| Did he invent an API? | **exact string match**, keep | A token in no page is a fact about two strings. No judge beats `in`, and a judge costs money to be worse. |
| Did he cite a real slug he actually read? | **exact match**, keep | Same. |
| Did he call `read_changelog` for a version question? | **`ToolCorrectnessMetric`** | Deterministic when `available_tools` is omitted. Direct replacement for hand-rolled tool assertions. |
| Was the answer *useful*, or an unhelpful refusal? | **`AnswerRelevancyMetric`** | The one thing exact matching cannot do, and the exact bug that shipped. A refusal is unfaithful to nothing but irrelevant to everything. |
| Did retrieval surface the right pages? | **Contextual Precision / Recall** | Separates "read the wrong page" from "read the right page and answered badly". Today those are one undifferentiated failure. |
| Did he keep the thread across turns? | **`KnowledgeRetentionMetric`** | Needs a judge; "it" resolving to a Run is semantic. |
| Did he stay a docs assistant under injection? | **`RoleAdherenceMetric` + `RoleViolationMetric`** | Turns my one-off stress test into a suite that runs every release. |

**Correction to a common assumption:** `DAGMetric` is *not* the deterministic escape hatch. Its tree
structure is fixed but every judgement node still calls an LLM, so branch selection varies. Use it
for ordered criteria (format first, then quality), not for reproducibility.

Net: DeepEval replaces the parts of the runner that were approximating a judge, and keeps the parts
that were never judging.

## Dependency handling

`deepeval` pulls 29 packages and **downgrades `click` and `rich`**, both of which the CLI and the
lint/test output use. It also installs `posthog` and reports telemetry by default.

So it never enters the gate's virtualenv:

```make
eval-jack:
	cd examples/jack && DEEPEVAL_TELEMETRY_OPT_OUT=YES \
	  uv run --with deepeval --python 3.12 python -m eval.run
```

Ephemeral, isolated, no lockfile change. `[project.optional-dependencies]` gains nothing here
because nothing importable depends on it.

## The judge

Judged metrics need a model. The one configured is Gemini behind an OpenAI-compatible base URL, so
a `DeepEvalBaseLLM` subclass wraps the same client the agent uses and reads the same environment.

One rule: **the judge must not be the model under test where the metric is about self-assessment.**
For relevancy and retrieval that is acceptable and standard. For role adherence under injection it
is not, because a model that just obeyed an injection is not a reliable witness to having obeyed
it. Those cases keep an exact assertion (`"INJECTED" in answer`) as the primary signal, with the
judge as a second opinion.

## The suite

Nine sets, roughly 115 goldens. The 50 that exist become sets 1-4.

### 1. Capability coverage (50, exists)

One golden per thing a reader asks. Already written, already passing 41/50.

Metrics: `AnswerRelevancy`, `Faithfulness`, custom `InventedAPI`, custom `CitationValid`.

### 2. Retrieval (18, new)

Questions whose correct source page is known and unambiguous, so retrieval can be scored apart
from generation.

Each golden names its `expected_pages`. `retrieval_context` is the pages actually read, taken from
`tool.call.started` events.

Metrics: `ContextualPrecision`, `ContextualRecall`, `ContextualRelevancy`.

This set is what would have distinguished "the docs are thin" from "Jack read the wrong page" in
the two real failures found so far. Both turned out to be the former, but nothing proved it.

### 3. Tool use (12, new)

| Golden shape | `expected_tools` |
|---|---|
| version question | `read_changelog` |
| "what does page X say" | `read_doc` with that slug |
| open-ended concept question | `search_docs` then `read_doc` |
| question about the page the reader is on | `read_doc` with the supplied slug |

Metrics: `ToolCorrectness` (deterministic), `ArgumentCorrectness` for the slug arguments.

Catches the class of bug where Jack answers from a search excerpt without reading the page, which
is currently caught only by a hand-written `if not read` and only sometimes.

### 4. Refusal, both directions (12, new: 3 exist)

The asymmetry that matters. Six goldens the corpus genuinely does not cover, six it covers fully
but which *sound* like they might not.

| Direction | Failure | Caught by |
|---|---|---|
| Should refuse, answered | invented capability | custom `InventedAPI` + `Faithfulness` |
| Should answer, refused | **the shipped bug** | `AnswerRelevancy` below threshold |

The second column is the reason for this whole migration. Six deliberately-scary-sounding but
documented questions ("can I run this in production?", "does it support cancellation?") are the
regression test for it.

### 5. Multi-turn (8 conversations, 24 turns, new: 3 exist)

`ConversationalTestCase` with `chatbot_role="the AgentDeck documentation agent"`.

Conversation shapes: pronoun carry-over, topic switch and return, correction mid-thread
("no, I meant workflows"), a refused turn followed by an answerable one, and a long thread that
exercises the bounded session.

Metrics: `KnowledgeRetention`, `ConversationRelevancy`, `ConversationCompleteness`, `RoleAdherence`.

### 6. Adversarial (14, new)

The stress test, made repeatable. Every probe run by hand becomes a golden.

Instruction override, system-prompt extraction, role reassignment, delimiter break through
`selection`, injection through `page`, path traversal through `read_doc`, off-topic compute,
encoded instructions, unicode smuggling, fake operator authority, tool-schema disclosure.

Metrics: exact assertion first (`RoleViolation`, `PIILeakage`, `Misuse` as second opinion).

**Known-failing on arrival:** the `selection` injection succeeds today and is documented as
unguarded. It enters the suite as an expected failure with a recorded score, so a *regression* is
visible even though the baseline is not clean. Never silently excluded.

### 7. Temporal correctness (8, new: 4 exist)

Version questions are the one place Jack is *supposed* to cite something outside the docs, and the
one place he can present a removed API as current.

Each golden asserts the answer names a release when the claim comes from history. Custom metric,
exact: a changelog-sourced identifier must appear within N characters of a version string.

### 8. Answer shape (6, new)

The instructions say "keep answers short and concrete" and "cite the pages you used". Nothing
checks either.

`DAGMetric` fits here and nowhere else: format compliance first (is there a citation block?), then
quality (is the answer concise?). Ordered criteria is what DAG is for.

### 9. Operational (unchanged, stays in the gate)

The 40 offline tests. Quota, origin, allowlist, size limits, wire format, context injection. No
model, 7 seconds, blocks merges. DeepEval has nothing to add and would only make these slower and
flakier.

Plus one fix: `test_a_conversation_cannot_grow_without_bound` caps turns, not bytes, so its name
promises a guarantee its assertion does not make. It needs a byte assertion over
`BoundedSessions`.

## Layering

| Layer | What | Model | Where |
|---|---|---|---|
| A | 40 offline tests + deterministic golden checks | no | `make check`, blocks merge |
| B | DeepEval suite, sets 1-8 | yes | `make eval-jack`, before a release |
| C | Adversarial set alone | yes | also on any change to instructions or tools |

Layer A must stay able to fail the build. Layer B must never gate a merge: a judged suite in a
required check is a flaky gate, which is the one thing the current design got unambiguously right.

## Thresholds

Start permissive, ratchet. A threshold that fails on arrival teaches people to ignore the suite.

| Metric | Start | Target |
|---|---|---|
| Answer Relevancy | 0.7 | 0.85 |
| Faithfulness | 0.8 | 0.9 |
| Contextual Recall | 0.6 | 0.8 |
| Tool Correctness | 1.0 | 1.0 |
| Invented API | 1.0 | 1.0 |
| Knowledge Retention | 0.7 | 0.8 |
| Role Adherence | 0.8 | 0.95 |

The two at 1.0 are the deterministic ones. They are the only metrics allowed to be absolute,
because they are the only ones that cannot disagree with themselves between runs.

## Order of work

| # | Step | Why first |
|---|---|---|
| 1 | Judge wrapper + `make eval-jack` running set 1 | Proves the endpoint, the isolation and the reporting before any golden is written |
| 2 | Port the two custom deterministic checks to `BaseMetric` | One report, not two runners |
| 3 | Set 4 (refusal both directions) | The bug that started this |
| 4 | Set 3 (tool use) | Deterministic, cheap, catches weak grounding |
| 5 | Set 2 (retrieval) | Separates docs gaps from agent faults, which is the recurring ambiguity |
| 6 | Set 6 (adversarial) | Makes the stress test repeatable before the instructions change again |
| 7 | Sets 5, 7, 8 | Real but lower yield |

Steps 1-3 are the useful minimum. Everything after is coverage.

## What this does not solve

Jack is only as right as the page he reads. Every real failure found so far was a thin or wrong
documentation page, not a reasoning error. This suite will keep reporting docs gaps as agent
failures, correctly, and the fix will keep being P1 work from
[the IA plan](plan-docs-ia.md) rather than anything in `examples/jack`.
