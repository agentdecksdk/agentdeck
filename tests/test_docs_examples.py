"""Stage 2 of the docs anti-rot suite (docs-site-plan.md §6): *execute* the runnable fences
stage 1 (``test_docs_site.py``) only parses. Two new fence-meta tokens, both opt-in  -  a fence
with neither keeps today's parse-only behavior:

- ``file=<relative-path>``  -  write this fence's source verbatim into the page's shared temp
  project, at ``<relative-path>``. Not executed by itself.
- ``run``  -  execute this fence as a real subprocess against that same temp project, once
  per fence.

A third token, ``illustrative reason="..."``, opts a fence *out* of execution on purpose
(needs a live server, a real DSN) instead of leaving it silently unexecuted  -  mirrors stage
1's ``no-test reason="..."`` escape hatch exactly.

A ``run`` fence executes as ``python <script>`` in its own process, cwd'd into the
assembled project, talking to a scripted OpenAI-Chat-Completions-compatible HTTP server
(``fake_model_server`` below) via ``OPENAI_BASE_URL`` / ``OPENAI_USE_RESPONSES``  -  the same
knobs getting-started.mdx tells a reader to set for a non-OpenAI endpoint. Nothing in
``agentdeck`` is patched: this is the path a reader's own shell actually takes, against the
repo's own already-installed package, and each fence gets a fresh interpreter, so there is
no in-process state (settings cache, ``sys.modules``) to leak between fences or into the
rest of the suite.
"""

from __future__ import annotations

import os
import subprocess
import sys
from functools import cache
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

import pytest
from test_docs_site import FENCE, REASON, _pages

from agentdeck.testing import scripted_model_server

if TYPE_CHECKING:
    from collections.abc import Iterator

_SUBPROCESS_TIMEOUT = 30

# Mirrors tests/golden/conftest.py::_PINNED_ENV in spirit: memory backends and no external
# services, so a run fence can never reach real Redis, Langfuse, or MCP. OPENAI_BASE_URL is
# added per test (once fake_model_server's ephemeral port is known) instead of living here;
# OPENAI_USE_RESPONSES=false is the same Chat-Completions flag getting-started.mdx documents
# for a non-OpenAI endpoint.
_PINNED_ENV = {
    "AGENTDECK_EVENTS": "memory://",
    "AGENTDECK_SESSION": "",
    "AGENTDECK_LANGFUSE_PUBLIC_KEY": "",
    "AGENTDECK_LANGFUSE_SECRET_KEY": "",
    "AGENTDECK_MCP_SERVERS": "{}",
    "OPENAI_API_KEY": "docs-example",
    "OPENAI_MODEL": "fake-docs-example",
    "OPENAI_USE_RESPONSES": "false",
}


@pytest.fixture(scope="module")
def fake_model_server() -> Iterator[str]:
    """One scripted endpoint for the whole module: stateless and canned, so every run
    fence shares it rather than paying for a server start/stop per fence. None of today's
    run fences branch on what they send, so a plain text reply proves the wire round-trip.
    """
    with scripted_model_server() as base_url:
        yield base_url


def _tokens(meta: str) -> list[str]:
    """Meta words, with any ``reason="..."`` blanked first so a space inside the quoted
    reason can't be mistaken for a second token (e.g. reason="needs a live server to run").
    """
    return REASON.sub("", meta).split()


@cache
def _fences_of(page: Path) -> tuple[list[tuple[int, str, str | None, str]], list[tuple[str, str]]]:
    """``(run fences, file fences)`` for one page: run as ``(fence-index, source, tool-name,
    tool-arguments)``, file as ``(relative-path, source)``  -  both in the order they appear on
    the page. ``tool="name"`` on a run fence scripts the model to call that tool on its first
    turn instead of answering in text, so the fence proves the tool's own body actually ran
    rather than only that the script built; ``tool_arguments='{"k":"v"}'`` (single-quoted, since
    the JSON payload already owns double quotes) supplies its call arguments, default ``{}``.
    """
    runs: list[tuple[int, str, str | None, str]] = []
    files: list[tuple[str, str]] = []
    for i, (_lang, meta, src) in enumerate(FENCE.findall(page.read_text())):
        tokens = _tokens(meta)
        file_token = next((t for t in tokens if t.startswith("file=")), None)
        if file_token is not None:
            files.append((file_token.removeprefix("file="), src))
        elif "run" in tokens:
            tool_token = next((t for t in tokens if t.startswith("tool=")), None)
            tool_name = tool_token.removeprefix("tool=").strip("'\"") if tool_token else None
            args_token = next((t for t in tokens if t.startswith("tool_arguments=")), None)
            tool_arguments = args_token.removeprefix("tool_arguments=").strip("'\"") if args_token else "{}"
            runs.append((i, src, tool_name, tool_arguments))
    return runs, files


def _run_cases() -> list[tuple[Path, int, str, str | None, str]]:
    return [
        (page, i, src, tool_name, tool_arguments)
        for page in _pages()
        for i, src, tool_name, tool_arguments in _fences_of(page)[0]
    ]


def _illustrative_cases() -> list[tuple[Path, str]]:
    return [
        (page, meta)
        for page in _pages()
        for _lang, meta, _src in FENCE.findall(page.read_text())
        if "illustrative" in _tokens(meta)
    ]


RUN_CASES = _run_cases()
ILLUSTRATIVE_CASES = _illustrative_cases()


def _write_file(root: Path, page: Path, relpath: str, src: str) -> None:
    rel = PurePosixPath(relpath)
    assert not rel.is_absolute(), f"{page.name}: file= path must be relative: {relpath}"
    assert ".." not in rel.parts, f"{page.name}: file= path escapes the project root: {relpath}"
    dest = root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(src)


def _execute(script: Path, cwd: Path, env: dict[str, str], page: Path, index: int) -> None:
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=_SUBPROCESS_TIMEOUT,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"{page.name} run fence #{index}: exit {result.returncode}\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )


@pytest.mark.parametrize(
    "page,index,src,tool_name,tool_arguments",
    RUN_CASES,
    ids=[f"{page.name}::run#{i}" for page, i, _, _, _ in RUN_CASES],
)
def test_run_fence_executes(
    page: Path,
    index: int,
    src: str,
    tool_name: str | None,
    tool_arguments: str,
    tmp_path: Path,
    fake_model_server: str,
) -> None:
    _, files = _fences_of(page)
    for relpath, file_src in files:
        _write_file(tmp_path, page, relpath, file_src)
    # a reader would have a file on disk, not a piped stdin script  -  same as the file= fences
    script = tmp_path / f"_run_{index}.py"
    script.write_text(src)
    env = dict(os.environ)
    env.update(_PINNED_ENV)
    if tool_name is None:
        env["OPENAI_BASE_URL"] = fake_model_server
        _execute(script, tmp_path, env, page, index)
    else:
        # a dedicated server, not the shared fixture: scripting a tool call here must not change
        # what every other page's fence gets back.
        with scripted_model_server(tool_name=tool_name, tool_arguments=tool_arguments) as base_url:
            env["OPENAI_BASE_URL"] = base_url
            _execute(script, tmp_path, env, page, index)


@pytest.mark.parametrize("page,meta", ILLUSTRATIVE_CASES, ids=[p.name for p, _ in ILLUSTRATIVE_CASES])
def test_illustrative_fences_have_reason(page: Path, meta: str) -> None:
    assert REASON.search(meta), f'{page.name}: illustrative needs reason="why this cannot run"'


__all__ = ["ILLUSTRATIVE_CASES", "RUN_CASES"]
