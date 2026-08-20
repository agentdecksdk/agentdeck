#!/usr/bin/env python3
"""Incremental slop check for Claude Code hooks.

Flags AI-agent comment smells in Python files: narrative comments
that restate the code (SLOP001), TODO/FIXME/HACK without an issue
reference (SLOP002), and placeholder comments masking unfinished work
(SLOP003). Only lines changed relative to git HEAD are enforced, so
pre-existing repository debt is ignored while all current-worktree debt
remains gated.

Changed text files also reject the spaced-dash substitute forbidden by the
repository style guide. Existing occurrences remain untouched until edited.

Two loops share this script: a PostToolUse hook checks the single edited
file (fast loop), and a Stop hook runs `--changed` over every supported text file
changed vs HEAD, catching files written via Bash or other processes that
the Edit|Write matcher never sees (completion loop). Violations go to
stderr with exit code 2, which Claude Code feeds back to the agent.
"""

from __future__ import annotations

import ast
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
    r"^#\s*(!|type:|noqa|ruff:|fmt:|pragma|ponytail:|slopcheck:|coding[:=]"
    r"|pyright:|mypy:|pylint:|isort:|coverage:)"
)
# A suppression that names no rule code silences everything, including what it
# never meant to silence; blanket forms are themselves slop.
BLANKET_RE = re.compile(r"^(noqa|type:\s*ignore|pyright:\s*ignore|ruff:\s*noqa)\s*$")
ALLOW_RE = re.compile(r"slopcheck:\s*allow\s+(SLOP\d{3})\b")
SKIP_MARK_RE = re.compile(r"pytest\.mark\.(?:skip(?!if)|xfail)")
ABSTRACT_BASES = ("Protocol", "ABC", "ABCMeta")
ABSTRACT_DECORATORS = frozenset({"abstractmethod", "overload", "override"})
DIVIDER_RE = re.compile(r"-{4,}|={4,}")
HUNK_RE = re.compile(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@", re.MULTILINE)
TEXT_SUFFIXES = frozenset(
    {
        ".css",
        ".html",
        ".http",
        ".js",
        ".json",
        ".lock",
        ".md",
        ".mdx",
        ".mjs",
        ".py",
        ".sh",
        ".svg",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".yaml",
        ".yml",
    }
)
EM_DASH = chr(0x2014)


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
    allowed: dict[int, set[str]] = {}
    for tok in all_tokens:
        if tok.type != tokenize.COMMENT:
            continue
        row, col = tok.start
        for rule_id in ALLOW_RE.findall(tok.string):
            allowed.setdefault(row, set()).add(rule_id)
        text = tok.string.lstrip("#").strip()
        if BLANKET_RE.match(text):
            violations.append(
                Violation(
                    row,
                    row,
                    "SLOP006 blanket-suppression",
                    f"{text!r} names no rule code; suppress the specific code or fix the finding",
                )
            )
            continue
        if MARKER_RE.match(tok.string) or DIVIDER_RE.search(text):
            continue
        trailing = bool(lines[row - 1][:col].strip())
        comments.append((row, col, text, trailing))

    for row, ln in enumerate(lines, 1):
        if SKIP_MARK_RE.search(ln) and not ISSUE_REF_RE.search(ln):
            violations.append(
                Violation(
                    row,
                    row,
                    "SLOP008 untracked-skip",
                    "skip/xfail weakens the suite silently; add reason= with an issue ref (#123)",
                )
            )

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

    violations.extend(_ast_violations(source))
    violations = [v for v in violations if not any(v.rule.startswith(r) for r in allowed.get(v.start, ()))]
    return sorted(violations, key=lambda v: v.start)


def check_style(source: str) -> list[Violation]:
    return [
        Violation(row, row, "SLOP009 em-dash", "replace the em dash with a colon, hyphen, or new sentence")
        for row, line in enumerate(source.splitlines(), 1)
        if EM_DASH in line
    ]


def _stub_body(stmts: list[ast.stmt]) -> bool:
    if (
        stmts
        and isinstance(stmts[0], ast.Expr)
        and isinstance(stmts[0].value, ast.Constant)
        and isinstance(stmts[0].value.value, str)
    ):
        stmts = stmts[1:]
    if len(stmts) != 1:
        return False
    stmt = stmts[0]
    if isinstance(stmt, ast.Pass):
        return True
    # `raise NotImplementedError` is exempt on purpose: this codebase uses it as the
    # informal-abstract-hook idiom (ControlSignalled._effect, Workflow.build_graph).
    return isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and stmt.value.value is Ellipsis


def _decorator_names(node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    names: set[str] = set()
    for dec in node.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Attribute):
            names.add(target.attr)
        elif isinstance(target, ast.Name):
            names.add(target.id)
    return names


def _ast_violations(source: str) -> list[Violation]:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    out: list[Violation] = []

    def visit(node: ast.AST, abstract: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                bases = {getattr(b, "id", None) or getattr(b, "attr", "") for b in child.bases}
                visit(child, any(b in ABSTRACT_BASES or b.endswith(("Protocol", "ABC")) for b in bases))
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                if not abstract and not (_decorator_names(child) & ABSTRACT_DECORATORS) and _stub_body(child.body):
                    out.append(
                        Violation(
                            child.lineno,
                            child.lineno,
                            "SLOP004 concrete-placeholder",
                            f"{child.name}() is a pass/... stub outside an abstract context; "
                            "implement it, or raise NotImplementedError if it is a subclass hook",
                        )
                    )
                visit(child, abstract)
            else:
                if isinstance(child, ast.Try):
                    # Broad catches only: a narrow typed except with pass/continue is a
                    # judgment call (deck.py's deferred-wake pattern); silence + Exception is not.
                    # Deliberate write-gate twin of ruff S110/S112, which run later at commit/CI;
                    # scope changes must land in both.
                    for handler in child.handlers:
                        names = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
                        broad = handler.type is None or any(
                            getattr(n, "id", None) in ("Exception", "BaseException") for n in names
                        )
                        if broad and all(isinstance(s, ast.Pass | ast.Continue) for s in handler.body):
                            out.append(
                                Violation(
                                    handler.lineno,
                                    handler.lineno,
                                    "SLOP005 swallowed-exception",
                                    "broad except silently swallowed; handle it, log it, or narrow and justify",
                                )
                            )
                visit(child, abstract)

    visit(tree, False)
    return out


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], capture_output=True, text=True, cwd=cwd, timeout=10)


def changed_lines(path: Path, base: str = "HEAD") -> set[int] | None:
    """Line numbers changed vs base; None means check every line (untracked or no git)."""
    try:
        if _git("ls-files", "--error-unmatch", str(path), cwd=path.parent).returncode != 0:
            return None
        diff = _git("diff", base, "-U0", "--", str(path), cwd=path.parent)
        if diff.returncode != 0:
            return None
    except (OSError, subprocess.SubprocessError):
        return None
    lines: set[int] = set()
    for start, count in HUNK_RE.findall(diff.stdout):
        first = int(start)
        lines.update(range(first, first + int(count or 1)))
    return lines


def _repo_root() -> Path:
    try:
        out = _git("rev-parse", "--show-toplevel").stdout.strip()
        return Path(out).resolve() if out else Path.cwd().resolve()
    except (OSError, subprocess.SubprocessError):
        return Path.cwd().resolve()


def _scope(path: Path, violations: list[Violation]) -> list[Violation]:
    # Library-only rules use the same predicate as concept_budget/quality_delta:
    # under <toplevel>/agentdeck/. The toplevel comes from the file's own git so a
    # worktree checkout resolves to its own root, not the session's.
    try:
        out = _git("rev-parse", "--show-toplevel", cwd=path.parent).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        out = ""
    if not out or Path(out).resolve() / "agentdeck" not in path.parents:
        return [v for v in violations if not v.rule.startswith("SLOP004")]
    return violations


def _outside_repo(path: Path) -> bool:
    """Hook modes gate this repo only: a session here editing another project's
    legacy file must not be blocked on lines nobody is writing."""
    root = _repo_root()
    return root != path and root not in path.parents


def check_file(path: Path, all_lines: bool = False, base: str = "HEAD") -> list[str]:
    path = path.resolve()
    source = path.read_text(encoding="utf-8")
    violations = check_style(source)
    if path.suffix == ".py":
        violations.extend(check_source(source))
    violations = _scope(path, violations)
    changed = None if all_lines else changed_lines(path, base)
    if changed is not None:
        violations = [v for v in violations if v.touches(changed)]
    return [f"{path}:{v.start}: {v.rule}: {v.message}" for v in violations]


def changed_text_files(base: str = "HEAD") -> list[Path]:
    try:
        root_proc = _git("rev-parse", "--show-toplevel")
        if root_proc.returncode != 0:
            return []
        root = Path(root_proc.stdout.strip())
        tracked = _git("diff", "--name-only", base, cwd=root).stdout.splitlines()
        untracked = _git("ls-files", "--others", "--exclude-standard", cwd=root).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return []
    return [root / p for p in {*tracked, *untracked} if Path(p).suffix in TEXT_SUFFIXES and (root / p).is_file()]


def main() -> int:
    args = sys.argv[1:]
    base = args[args.index("--base") + 1] if "--base" in args else "HEAD"
    if base != "HEAD":
        # Merge-base, same as concept_budget/quality_delta: a base branch that advanced
        # after the PR forked must not leak its own changes into what "this PR added".
        merge_base = _git("merge-base", base, "HEAD").stdout.strip()
        base = merge_base or base
    argv_paths = [a for a in args if not a.startswith("-") and a != base]
    if "--write" in args:
        # PreToolUse: reconstruct what the file would become and deny the write
        # (exit 2) before slop ever reaches disk. Only newly introduced lines count.
        try:
            payload = json.load(sys.stdin)
        except (json.JSONDecodeError, ValueError):
            return 0
        tool_input = payload.get("tool_input", {})
        raw = tool_input.get("file_path", "")
        if Path(raw).suffix not in TEXT_SUFFIXES:
            return 0
        path = Path(raw)
        if _outside_repo(path.resolve()):
            return 0
        previous = path.read_text(encoding="utf-8") if path.is_file() else ""
        if "content" in tool_input:
            candidate = tool_input["content"]
        elif "new_string" in tool_input:
            old = tool_input.get("old_string", "")
            if not old or old not in previous:
                # Stale or mismatched old_string: the Edit itself will fail, and the
                # PostToolUse belt re-checks whatever actually lands on disk.
                return 0
            count = -1 if tool_input.get("replace_all") else 1
            candidate = previous.replace(old, tool_input["new_string"], count)
        else:
            return 0
        previous_lines = set(previous.splitlines())
        new_rows = {i for i, ln in enumerate(candidate.splitlines(), 1) if ln not in previous_lines}
        violations = check_style(candidate)
        if path.suffix == ".py":
            violations.extend(check_source(candidate))
        blocked = [v for v in _scope(path.resolve(), violations) if v.touches(new_rows)]
        for v in blocked:
            print(f"{path}:{v.start}: {v.rule}: {v.message}", file=sys.stderr)
        return 2 if blocked else 0
    if "--changed" in args:
        if not sys.stdin.isatty():
            try:
                payload = json.load(sys.stdin)
            except (json.JSONDecodeError, ValueError):
                payload = {}
            if payload.get("stop_hook_active"):
                return 0
        reports = [line for path in changed_text_files(base) for line in check_file(path, base=base)]
    elif argv_paths:
        path = Path(argv_paths[0])
        if path.suffix not in TEXT_SUFFIXES or not path.is_file():
            return 0
        reports = check_file(path, all_lines="--all" in args)
    else:
        try:
            payload = json.load(sys.stdin)
        except (json.JSONDecodeError, ValueError):
            return 0
        raw = payload.get("tool_input", {}).get("file_path", "")
        path = Path(raw) if raw else None
        if path is None or path.suffix not in TEXT_SUFFIXES or not path.is_file() or _outside_repo(path.resolve()):
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
    directives = (
        "x = 1  # noqa: E501\ny = 2  # type: ignore[arg-type]\nz = 3  # pyright: ignore[reportGeneralTypeIssues]\n"
    )
    assert not check_source(directives), "coded suppressions must pass"
    blanket = "x = 1  # noqa\n"
    assert any(v.rule.startswith("SLOP006") for v in check_source(blanket)), "blanket noqa must be flagged"
    blanket_ty = "y = 2  # type: ignore\n"
    assert any(v.rule.startswith("SLOP006") for v in check_source(blanket_ty)), "bare type: ignore must be flagged"
    stub_fn = "class SqliteRunStore:\n    def save(self, run):\n        pass\n"
    assert any(v.rule.startswith("SLOP004") for v in check_source(stub_fn)), "concrete stub must be flagged"
    protocol = "class RunStore(Protocol):\n    def save(self, run) -> None: ...\n"
    assert not check_source(protocol), "Protocol stubs must pass"
    abstract = "class Base(ABC):\n    @abstractmethod\n    def save(self):\n        pass\n"
    assert not check_source(abstract), "abstractmethod stubs must pass"
    informal = "class Signal:\n    def effect(self):\n        raise NotImplementedError\n"
    assert not check_source(informal), "NotImplementedError subclass hooks must pass"
    swallow = "try:\n    x = 1\nexcept Exception:\n    pass\n"
    assert any(v.rule.startswith("SLOP005") for v in check_source(swallow)), "swallowed exception must be flagged"
    handled = "try:\n    x = 1\nexcept ValueError:\n    logger.warning('bad value')\n"
    assert not check_source(handled), "handled exception must pass"
    skip = "@pytest.mark.skip(reason='broken')\ndef test_x(): assert run()\n"  # slopcheck: allow SLOP008 fixture
    assert any(v.rule.startswith("SLOP008") for v in check_source(skip)), "untracked skip must be flagged"
    skip_ref = "@pytest.mark.xfail(reason='known engine bug #311')\ndef test_y(): assert run()\n"
    assert not check_source(skip_ref), "issue-referenced xfail must pass"
    skipif = "@pytest.mark.skipif(sys.platform == 'win32', reason='posix only')\ndef test_z(): assert run()\n"
    assert not check_source(skipif), "conditional skipif must pass"
    allow = "# Increment the retry count  (slopcheck: allow SLOP001 exemplar fixture)\nretry_count += 1\n"
    assert not check_source(allow), "explicit coded allow marker must suppress"
    em_dash = "Use the short path" + chr(0x2014) + "the runtime owns the machinery.\n"
    assert any(v.rule.startswith("SLOP009") for v in check_style(em_dash)), "em dash must be flagged"
    assert not check_style("Use the short path: the runtime owns the machinery.\n"), "colon must pass"
    assert {".css", ".json", ".sh", ".ts", ".tsx"} <= TEXT_SUFFIXES, "repository text must route to SLOP009"
    print("slopcheck self-test: ok")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        _self_test()
        sys.exit(0)
    sys.exit(main())
