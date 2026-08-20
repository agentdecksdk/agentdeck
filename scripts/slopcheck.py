#!/usr/bin/env python3
"""Incremental slop check for Claude Code hooks.

Flags three AI-agent comment smells in Python files: narrative comments
that restate the code (SLOP001), TODO/FIXME/HACK without an issue
reference (SLOP002), and placeholder comments masking unfinished work
(SLOP003). Only lines changed relative to git HEAD are enforced, so
pre-existing repository debt is ignored while all current-worktree debt
remains gated.

Two loops share this script: a PostToolUse hook checks the single edited
file (fast loop), and a Stop hook runs `--changed` over every Python file
changed vs HEAD, catching files written via Bash or other processes that
the Edit|Write matcher never sees (completion loop). Violations go to
stderr with exit code 2, which Claude Code feeds back to the agent.
"""

from __future__ import annotations

import io
import json
import re
import subprocess
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path

STOPWORDS = frozenset(
    [
        "the",
        "a",
        "an",
        "to",
        "of",
        "for",
        "in",
        "on",
        "and",
        "or",
        "is",
        "are",
        "it",
        "its",
        "this",
        "that",
        "we",
        "with",
        "then",
        "if",
        "each",
        "all",
        "into",
        "from",
        "as",
        "by",
        "at",
        "be",
        "not",
        "no",
        "now",
        "when",
        "will",
        "new",
        "through",
        "also",
        "only",
        "here",
        "there",
        "some",
        "any",
        "own",
    ]
)
# Verbs that narrate mechanics rather than intent; they count as matched even
# though the operator they describe has no identifier to match against.
GENERIC_VERBS = frozenset(
    [
        "increment",
        "decrement",
        "initialize",
        "init",
        "create",
        "instantiate",
        "loop",
        "iterate",
        "call",
        "invoke",
        "return",
        "set",
        "get",
        "check",
        "define",
        "add",
        "append",
        "import",
        "assign",
        "update",
        "declare",
        "compute",
        "calculate",
    ]
)
TODO_RE = re.compile(r"\b(TODO|FIXME|HACK)\b", re.IGNORECASE)
ISSUE_REF_RE = re.compile(r"#\d+|https?://github\.com/[^/\s]+/[^/\s]+/issues/\d+")
PLACEHOLDER_RE = re.compile(
    r"^placeholder\b"
    r"|implement(?:ed)? later"
    r"|rest of (?:the )?(?:code|file|function)"
    r"|(?:existing|remaining) (?:code|logic) remains? (?:the same|unchanged)"
    r"|your (?:code|logic) here",
    re.IGNORECASE,
)
# Tool directives, deliberate-shortcut markers, and section dividers are not prose.
MARKER_RE = re.compile(
    r"^#\s*(!|type:|noqa|ruff:|fmt:|pragma|ponytail:|coding[:=]"
    r"|pyright:|mypy:|pylint:|isort:|coverage:)"
)
DIVIDER_RE = re.compile(r"-{4,}|={4,}")
HUNK_RE = re.compile(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@", re.MULTILINE)


@dataclass(frozen=True)
class Violation:
    start: int
    end: int
    rule: str
    message: str

    def touches(self, changed: set[int]) -> bool:
        return any(row in changed for row in range(self.start, self.end + 1))


def _narrative(start: int, end: int, text: str) -> Violation:
    return Violation(
        start,
        end,
        "SLOP001 narrative-comment",
        f"{text!r}, delete unless it preserves non-obvious rationale or an invariant",
    )


def _comment_words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z]+", text.lower()) if w not in STOPWORDS and len(w) > 1]


def _code_tokens(line: str) -> set[str]:
    tokens: set[str] = set()
    for ident in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", line):
        for piece in ident.split("_"):
            tokens.update(w.lower() for w in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])", piece))
    return tokens


def _is_narrative(comment: str, code_line: str) -> bool:
    words = _comment_words(comment)
    if len(words) < 2:
        return False
    tokens = _code_tokens(code_line)
    matched = sum(
        1
        for w in words
        if w in GENERIC_VERBS
        or w.rstrip("s") in GENERIC_VERBS
        or w in tokens
        or w.rstrip("s") in tokens
        or w + "s" in tokens
    )
    # ponytail: bag-of-words heuristic, misses narration whose nouns are absent from
    # the code line ("create a new list"); upgrade to an LLM judge if that tail matters.
    return matched / len(words) >= 0.6


def check_source(source: str) -> list[Violation]:
    try:
        all_tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return []
    lines = source.splitlines()
    violations: list[Violation] = []

    comments: list[tuple[int, int, str, bool]] = []
    for tok in all_tokens:
        if tok.type != tokenize.COMMENT or MARKER_RE.match(tok.string):
            continue
        row, col = tok.start
        text = tok.string.lstrip("#").strip()
        if DIVIDER_RE.search(text):
            continue
        trailing = bool(lines[row - 1][:col].strip())
        comments.append((row, col, text, trailing))

    for row, _col, text, _trailing in comments:
        if TODO_RE.search(text) and not ISSUE_REF_RE.search(text):
            violations.append(
                Violation(
                    row,
                    row,
                    "SLOP002 untracked-todo",
                    f"{text!r}, add an issue ref (#123 or a GitHub issues URL)",
                )
            )
        elif PLACEHOLDER_RE.search(text):
            violations.append(Violation(row, row, "SLOP003 placeholder-comment", f"{text!r}, masks unfinished work"))

    # Full-line comments on consecutive rows form one block, judged as a whole
    # against the code line that follows; a fragment of a long why-comment would
    # otherwise be compared, and lose, on its own.
    flagged = {v.start for v in violations}
    blocks: list[list[tuple[int, str]]] = []
    for row, col, text, trailing in comments:
        if row in flagged:
            continue
        if trailing:
            code = lines[row - 1][:col].strip()
            if _is_narrative(text, code):
                violations.append(_narrative(row, row, text))
            continue
        if blocks and blocks[-1][-1][0] == row - 1:
            blocks[-1].append((row, text))
        else:
            blocks.append([(row, text)])
    for block in blocks:
        last_row = block[-1][0]
        code = next((ln for ln in lines[last_row:] if ln.strip() and not ln.lstrip().startswith("#")), "")
        joined = " ".join(text for _, text in block)
        if code and _is_narrative(joined, code):
            violations.append(_narrative(block[0][0], last_row, joined))

    return sorted(violations, key=lambda v: v.start)


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], capture_output=True, text=True, cwd=cwd, timeout=10)


def changed_lines(path: Path) -> set[int] | None:
    """Line numbers changed vs HEAD; None means check every line (untracked or no git)."""
    try:
        if _git("ls-files", "--error-unmatch", str(path), cwd=path.parent).returncode != 0:
            return None
        diff = _git("diff", "HEAD", "-U0", "--", str(path), cwd=path.parent)
        if diff.returncode != 0:
            return None
    except (OSError, subprocess.SubprocessError):
        return None
    lines: set[int] = set()
    for start, count in HUNK_RE.findall(diff.stdout):
        first = int(start)
        lines.update(range(first, first + int(count or 1)))
    return lines


def check_file(path: Path) -> list[str]:
    path = path.resolve()
    violations = check_source(path.read_text(encoding="utf-8"))
    changed = changed_lines(path)
    if changed is not None:
        violations = [v for v in violations if v.touches(changed)]
    return [f"{path}:{v.start}: {v.rule}: {v.message}" for v in violations]


def changed_py_files() -> list[Path]:
    try:
        root_proc = _git("rev-parse", "--show-toplevel")
        if root_proc.returncode != 0:
            return []
        root = Path(root_proc.stdout.strip())
        tracked = _git("diff", "--name-only", "HEAD", cwd=root).stdout.splitlines()
        untracked = _git("ls-files", "--others", "--exclude-standard", cwd=root).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return []
    return [root / p for p in {*tracked, *untracked} if p.endswith(".py") and (root / p).is_file()]


def main() -> int:
    argv_paths = [a for a in sys.argv[1:] if not a.startswith("-")]
    if "--changed" in sys.argv:
        if not sys.stdin.isatty():
            try:
                if json.load(sys.stdin).get("stop_hook_active"):
                    return 0
            except (json.JSONDecodeError, ValueError):
                pass
        reports = [line for path in changed_py_files() for line in check_file(path)]
    elif argv_paths:
        path = Path(argv_paths[0])
        if path.suffix != ".py" or not path.is_file():
            return 0
        reports = check_file(path)
    else:
        try:
            payload = json.load(sys.stdin)
        except (json.JSONDecodeError, ValueError):
            return 0
        raw = payload.get("tool_input", {}).get("file_path", "")
        path = Path(raw) if raw else None
        if path is None or path.suffix != ".py" or not path.is_file():
            return 0
        reports = check_file(path)
    for line in reports:
        print(line, file=sys.stderr)
    return 2 if reports else 0


def _self_test() -> None:
    narrative = "# Increment the retry count\nretry_count += 1\n"
    assert check_source(narrative), "narrative comment must be flagged"
    span = "# Increment\n# the retry count\nretry_count += 1\n"
    (v,) = check_source(span)
    assert (v.start, v.end) == (1, 2), "block violation must span every comment line"
    trailing = "results = []  # create a new results list\n"
    assert check_source(trailing), "trailing narrative comment must be flagged"
    why = (
        "# Before the raise, because the raise is what records the effect: an intent\n"
        "# left pending behind an honored one would be honored a second time.\n"
        "raise IntentHonored(intent)\n"
    )
    assert not check_source(why), "multi-line why-comment must pass as one block"
    todo = "# TODO: handle timeouts\nx = 1\n"
    assert check_source(todo), "untracked TODO must be flagged"
    tracked = "# TODO(#412): handle timeouts\nx = 1\n"
    assert not check_source(tracked), "tracked TODO must pass"
    url_todo = "# TODO: fix this https://google.com\nx = 1\n"
    assert check_source(url_todo), "TODO with a non-issue URL must be flagged"
    stub = "# placeholder for the real parser\n"
    assert check_source(stub), "placeholder stub must be flagged"
    elided = "# rest of the function\n"
    assert check_source(elided), "elision comment must be flagged"
    invariant = "# Wire format remains unchanged for backward compatibility.\npayload = encode_v1(data)\n"
    assert not check_source(invariant), "rationale mentioning 'remains unchanged' must pass"
    prose = "# PyPI refuses the name as too similar to a squatted placeholder package.\nNAME = 'agentdeck-sdk'\n"
    assert not check_source(prose), "prose mentioning 'placeholder' mid-sentence must pass"
    divider = "# --- context= accepts a type and a value -----------------\ncontext = 1\n"
    assert not check_source(divider), "section dividers must pass"
    directives = "x = 1  # noqa: E501\ny = 2  # type: ignore\nz = 3  # pyright: ignore\n"
    assert not check_source(directives), "tool directives must pass"
    print("slopcheck self-test: ok")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        _self_test()
        sys.exit(0)
    sys.exit(main())
