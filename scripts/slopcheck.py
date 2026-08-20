#!/usr/bin/env python3
"""Per-edit slop check, wired as a Claude Code PostToolUse hook.

Flags three AI-agent comment smells in the edited Python file:
narrative comments that restate the code, TODO/FIXME/HACK without an
issue reference, and placeholder comments masking unfinished work.
Only lines changed relative to git HEAD are reported, so legacy code
never nags. Violations go to stderr with exit code 2, which Claude Code
feeds back to the writing agent; clean files exit 0 silently.
"""

from __future__ import annotations

import io
import json
import re
import subprocess
import sys
import tokenize
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
ISSUE_REF_RE = re.compile(r"#\d+|https?://")
PLACEHOLDER_RE = re.compile(
    r"^placeholder\b|implement(ed)? later|rest of (the )?(code|file|function)"
    r"|remains? (the same|unchanged)|your (code|logic) here",
    re.IGNORECASE,
)
# Tool directives, deliberate-shortcut markers, and section dividers are not prose.
MARKER_RE = re.compile(r"^#\s*(!|type:|noqa|ruff:|fmt:|pragma|ponytail:|coding[:=])")
DIVIDER_RE = re.compile(r"-{4,}|={4,}")
HUNK_RE = re.compile(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@", re.MULTILINE)


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


def check_source(source: str) -> list[tuple[int, str]]:
    try:
        all_tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return []
    lines = source.splitlines()
    violations: list[tuple[int, str]] = []

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
            violations.append((row, f"untracked TODO/FIXME/HACK, add an issue ref (#123): {text!r}"))
        elif PLACEHOLDER_RE.search(text):
            violations.append((row, f"placeholder comment masking unfinished work: {text!r}"))

    # Full-line comments on consecutive rows form one block, judged as a whole
    # against the code line that follows; a fragment of a long why-comment would
    # otherwise be compared, and lose, on its own.
    flagged = {row for row, _ in violations}
    blocks: list[list[tuple[int, str]]] = []
    for row, col, text, trailing in comments:
        if row in flagged:
            continue
        if trailing:
            code = lines[row - 1][:col].strip()
            if _is_narrative(text, code):
                violations.append((row, f"narrative comment restates the code, delete it: {text!r}"))
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
            violations.append((block[0][0], f"narrative comment restates the code, delete it: {joined!r}"))

    return sorted(violations)


def changed_lines(path: Path) -> set[int] | None:
    """Line numbers changed vs HEAD; None means check every line (untracked or no git)."""
    try:
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(path)],
            capture_output=True,
            cwd=path.parent,
            timeout=10,
        )
        if tracked.returncode != 0:
            return None
        diff = subprocess.run(
            ["git", "diff", "HEAD", "-U0", "--", str(path)],
            capture_output=True,
            text=True,
            cwd=path.parent,
            timeout=10,
        )
        if diff.returncode != 0:
            return None
    except (OSError, subprocess.SubprocessError):
        return None
    lines: set[int] = set()
    for start, count in HUNK_RE.findall(diff.stdout):
        first = int(start)
        lines.update(range(first, first + int(count or 1)))
    return lines


def main() -> int:
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
    else:
        try:
            payload = json.load(sys.stdin)
        except (json.JSONDecodeError, ValueError):
            return 0
        raw = payload.get("tool_input", {}).get("file_path", "")
        if not raw:
            return 0
        path = Path(raw)
    if path.suffix != ".py" or not path.is_file():
        return 0
    path = path.resolve()
    violations = check_source(path.read_text(encoding="utf-8"))
    changed = changed_lines(path)
    if changed is not None:
        violations = [(row, msg) for row, msg in violations if row in changed]
    for row, message in violations:
        print(f"{path}:{row}: {message}", file=sys.stderr)
    return 2 if violations else 0


def _self_test() -> None:
    narrative = "# Increment the retry count\nretry_count += 1\n"
    assert check_source(narrative), "narrative comment must be flagged"
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
    stub = "# placeholder for the real parser\n"
    assert check_source(stub), "placeholder stub must be flagged"
    prose = "# PyPI refuses the name as too similar to a squatted placeholder package.\nNAME = 'agentdeck-sdk'\n"
    assert not check_source(prose), "prose mentioning 'placeholder' mid-sentence must pass"
    divider = "# --- context= accepts a type and a value -----------------\ncontext = 1\n"
    assert not check_source(divider), "section dividers must pass"
    directives = "x = 1  # noqa: E501\ny = 2  # type: ignore\n"
    assert not check_source(directives), "tool directives must pass"
    print("slopcheck self-test: ok")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        _self_test()
        sys.exit(0)
    sys.exit(main())
