# Comments

A comment preserves knowledge the code cannot carry: a why, an invariant, a surprising external behavior. Never what the next line does.

Good (real, `core/control.py`):

```python
# Before the raise, because the raise is what records the effect: an intent left
# pending behind an honored one would be honored a second time on the next resume.
```

Good (real, `core/reporting.py`):

```python
# Newest dropped, not oldest: a progress sequence missing its front is a run that
# appears to start at step 40, worse than one that stops reporting.
```

Bad (blocked by `scripts/slopcheck.py` before it reaches disk):

```python
# Increment the retry count
retry_count += 1
```

Rules:
- Zero comments is the default; earn each one.
- Ordering, race, and compatibility constraints are the usual legitimate subjects.
- Deliberate shortcuts carry `# ponytail: <ceiling and upgrade path>`.
- Suppressions carry a code and a reason (`# noqa: F401`, never bare `# noqa`).
- Two lines is the ceiling. A third means it is documentation: write it in `docs/` and link (SLOP011).

## Docstrings

A docstring says what the callable is for and what a caller must know to use it. The design behind it lives in `docs/design/`.

| Part | Budget |
|---|---|
| Summary line | always |
| Prose after it | 5 lines, total 6 (SLOP010) |
| `Args:`/`Returns:`/`Raises:`/`Attributes:`/`Examples:` | unbudgeted, they are per-item structure |
| Indented code example | unbudgeted |

`Notes:` is not a section, it is prose under a heading, and counts against the budget.
