# SOTA Findings: Keeping Agent-Written Code Aligned With Repo Style

**Date:** 2026-08-20
**Scope:** Web research on non-obvious, state-of-the-art techniques against AI code smell (comment bloat, autonomous LOC, pattern drift). Filtered against what this repo already runs: CLAUDE.md rulebook, import-linter, `make check`, reviewer agents.

## 1. Pair every rule with a repo exemplar

[Show and Tell (arXiv 2511.13972)](https://arxiv.org/abs/2511.13972) compared style-control strategies across multi-turn generation:

| Strategy | Initial adherence | Holds across turns |
| --- | --- | --- |
| Instructions only (our CLAUDE.md today) | strong | decays |
| Examples only | modest | none |
| Instruction + concrete exemplar paired | strongest | only strategy that held |

Key result: initial adherence and multi-turn discipline are separate properties. A rulebook that works on turn 1 says nothing about turn 5. Action implied: each CLAUDE.md ruling gets a short snippet from this repo showing the rule obeyed.

## 2. Slop-specific deterministic linters

A new tool category targeting rules classic linters cannot express. All deterministic, no LLM at runtime, fast enough for a per-edit hook.

| Tool | Catches | Runs |
| --- | --- | --- |
| [sloplint](https://sloplint.com/) | Narrative comments, fake fallbacks, swallowed exceptions, production TODOs; 50+ rules, 7 languages | CLI <1s, CI action |
| [antislop](https://github.com/skew202/antislop) | Hedging, deferrals, placeholder code | Multi-language CLI |
| [AI-SLOP-Detector](https://github.com/flamehaven01/ai-slop-detector) | Empty functions, fake docs, inflated comments | Offline CLI |
| [sloppylint](https://github.com/rsionnach/sloppylint) | Python: over-engineering, hallucinated imports, dead code | CLI |

Placement matters more than the tool: wired as a PostToolUse hook whose violations return as tool_result text, the agent self-corrects while the code is still in its working context. A CI failure an hour later gets a lazy patch; a rejection seconds after the Edit gets a real fix. See [hooks production patterns](https://www.pixelmojo.io/blogs/claude-code-hooks-production-quality-ci-cd-patterns) and the [hooks lifecycle guide](https://hidekazu-konishi.com/entry/claude_code_hooks_complete_guide.html). [ContextCov (arXiv 2603.00822)](https://arxiv.org/pdf/2603.00822) proposes compiling instruction files into executable runtime monitors on the same premise (early-stage, no verified numbers).

## 3. Prose rules as lint: gptlint

[gptlint](https://github.com/gptlint/gptlint) enforces rules written in plain markdown ("comments state a why the code can't") via LLM evaluation with structured violation output, runnable in CI. Covers the band between "AST can check it" and "only a human can check it". [Its own docs](https://gptlint.dev/project/limitations) admit false positives and misses, so: review signal, never a hard gate.

## 4. Preference-tuning on repo taste

[CodeUltraFeedback (arXiv 2403.09032)](https://arxiv.org/pdf/2403.09032): DPO on coding-style preferences made a 7B model beat 33B models on style adherence. Style is an alignment problem; accepted-vs-rejected review diffs are free training pairs. Impractical here today, noted as the frontier.

## 5. Exemplar retrieval before generation

The [repo-level RAG survey (arXiv 2510.04905)](https://arxiv.org/html/2510.04905v1) and [KG-based repo generation (arXiv 2505.14394)](https://arxiv.org/html/2505.14394v1): style consistency improves when the harness retrieves similar existing code into context before generation. The model's strongest style channel is imitation; most harnesses feed it nothing to imitate. Same mechanism explains finding 1.

## Synthesis

Prompt compliance is probabilistic, harness constraints are deterministic; spend effort on the second ([harness engineering guide](https://www.augmentcode.com/guides/harness-engineering-ai-coding-agents)). Novel for this repo specifically:

1. Exemplar-paired CLAUDE.md rules (finding 1).
2. Slop linter in a PostToolUse feedback loop (finding 2).
3. gptlint for prose-only rules between ruff and human review (finding 3).
