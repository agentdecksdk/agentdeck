# Harness Change Log

Running log of anti-slop harness changes, one row per change. Research basis: [sota_style_alignment_findings.md](sota_style_alignment_findings.md), [ai_agent_repository_alignment_research.md](ai_agent_repository_alignment_research.md), [solutions_for_aligning_ai_agents.md](solutions_for_aligning_ai_agents.md).

| Change | Where | Why |
| --- | --- | --- |
| `scripts/slopcheck.py`: deterministic checker flagging narrative comments, untracked TODO/FIXME/HACK, placeholder comments | `scripts/` | The three comment smells ruff cannot express; stdlib-only script we own beats an unvetted external slop linter |
| Checker reports only lines changed vs git HEAD | same script | Gate new debt, not history: agents must not be nagged about legacy lines they did not write |
| PostToolUse hook running the checker on every Edit/Write | `.claude/settings.json` | Feedback seconds after the edit, while the code is in the agent's context, gets a real fix; a CI failure later gets a lazy patch |
| Comments rule paired with a real good/bad exemplar from `core/control.py` | `CLAUDE.md` section 3 | Show and Tell (arXiv 2511.13972): instructions alone decay across turns; instruction + exemplar is the only strategy that holds |
| Errors rule paired with a real good/bad exemplar from the langgraph engine | `CLAUDE.md` section 3 | Same mechanism as above |

| v0.1 review fixes: placeholder rule requires stub context ("remains unchanged" rationale passes), violations carry line spans so editing any line of a comment block re-triggers it, rule IDs (SLOP001-003), issue refs must be `#N` or a GitHub issues URL | same script | Review findings: two correctness bugs, plus stable IDs for future suppression and metrics |
| Stop hook running `slopcheck --changed` over every Python file changed vs HEAD | `.claude/settings.json` | Edit/Write hooks never see files written via Bash or other processes; the completion loop catches them without taxing every Bash call |

Heuristic ceilings (deliberate): narration whose nouns are absent from the code line ("create a new list") passes; upgrade path is an LLM judge, only if the miss rate matters in practice.

| `scripts/repomap.py`: compact public-API map (griffe, dev dependency), one signature per line grouped by module | `scripts/`, `pyproject.toml` dev group | An agent cannot reuse what it cannot find; the whole map is ~4k tokens so it fits in context without Aider-style ranking |
| deck-dev must run the map and post a `## Reuse analysis` in the PR body before the first edit | `.claude/agents/deck-dev.md` | Duplicate abstractions are the next slop class after comments (intra-repo cloning study); the gate forces the question, the map makes it answerable |
| deck-reviewer gets a Reuse & Duplication dimension: missing analysis or overlap with an existing symbol is request-changes | `.claude/agents/deck-reviewer.md` | The dev's own reuse analysis becomes reviewable evidence |

| Repo map lines carry first-line docstrings | `scripts/repomap.py` | Names alone don't state responsibility; the docstring column turns the flat symbol spread into a responsibility index without a call graph's cost |

| `scripts/quality_delta.py`: per-PR structural cost vs merge base (code LOC, new public symbols, comments, TODOs, dependency lines); deck-reviewer runs it and challenges out-of-proportion axes | `scripts/`, `.claude/agents/deck-reviewer.md` | Agents inflate exactly these axes; a delta line makes 600-line-diff growth a one-glance fact. Measurement, not a gate: thresholds only after real baselines exist |
| `deck-cleanup` agent: one narrow scan per run (narrative-comments via `slopcheck --all`, dead-code, duplicate-helpers, pattern-drift), one evidence-backed small PR | `.claude/agents/deck-cleanup.md`, `slopcheck.py --all` flag | OpenAI's lesson: reviewed changes still accumulate entropy; a recurring restoring force beats Friday cleanup days |

| CI `slop` job: slopcheck over PR-added lines, reuse-analysis-required check when public symbols are added, quality delta in the job log | `.github/workflows/ci.yml`, `slopcheck.py --base` flag | Hooks only cover Claude Code sessions; CI covers outside contributors and anything that slipped the hooks, and makes the reuse gate mechanical instead of instructional |
| ruff gains the structural slop set: ERA (commented-out code), S110/S112 (swallowed exceptions), PIE790 (placeholder bodies) | `pyproject.toml` | Native rules before custom code; all four pass on the existing tree so they gate only new debt |
| YAGNI rule exemplar-paired (Run.cancel() vs CancellationManager) | `CLAUDE.md` section 3 | Third paired rule; the duplicate-abstraction case now has a named anti-pattern |
| CodeRabbit gets an anti-slop instruction scoped to judgment calls | `.coderabbit.yaml` | The semantic layer reviews only what ruff and slopcheck cannot express |
| deck-reviewer promotion loop: a repeat finding class must name the mechanical form that would have caught it | `.claude/agents/deck-reviewer.md` | Repeated review feedback is a harness gap; promote it to a rule instead of re-litigating |

Upgrade shelf, each layer activates on evidence: ast-grep when a structural anti-pattern repeats (rule one = that pattern); graph tools (code-review-graph family, after vetting) when the repo map outgrows one read or reuse analyses fail for lack of relationship queries; sloplint's remaining rules (hardcoded URLs, fake fallbacks, unsafe casts) when agents commit those here; gptlint only if prose-rule violations keep surviving both CodeRabbit and deck-reviewer.

Next planned actions: none queued; run the stack on real issues and let the evidence pick the next layer.
