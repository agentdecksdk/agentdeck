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

Next planned actions, in order: reuse-evidence gate in deck-dev, repo map, recurring cleanup agents.
