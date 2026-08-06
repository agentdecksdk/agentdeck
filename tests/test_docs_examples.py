"""Stage 2 of the docs anti-rot suite (docs-site-plan.md §6): *execute* the runnable fences
stage 1 (``test_docs_site.py``) only parses. Two new fence-meta tokens, both opt-in — a fence
with neither keeps today's parse-only behavior:

- ``file=<relative-path>`` — write this fence's source verbatim into the page's shared temp
  project, at ``<relative-path>``. Not executed by itself.
- ``run`` — execute this fence in-process against that same temp project, once per fence.

A third token, ``illustrative reason="..."``, opts a fence *out* of execution on purpose
(needs a live server, a real DSN) instead of leaving it silently unexecuted — mirrors stage
1's ``no-test reason="..."`` escape hatch exactly.
"""

from __future__ import annotations

import sys
from functools import cache
from pathlib import Path, PurePosixPath

import pytest
from scripted_model import ScriptedModel, provider_of
from test_docs_site import FENCE, REASON, _pages

from agentdeck.runtime.settings import reset_settings_cache

# Mirrors tests/golden/conftest.py::_PINNED_ENV: memory backends and no external services, so
# an executed example can never reach real Redis, Langfuse, MCP, or a model endpoint.
_PINNED_ENV = {
    "AGENTDECK_CHECKPOINT_BACKEND": "memory",
    "AGENTDECK_CHECKPOINT_URL": "",
    "AGENTDECK_EVENTS_BACKEND": "memory",
    "AGENTDECK_EVENTS_URL": "",
    "AGENTDECK_SESSION_REDIS_URL": "",
    "AGENTDECK_LANGFUSE_PUBLIC_KEY": "",
    "AGENTDECK_LANGFUSE_SECRET_KEY": "",
    "AGENTDECK_MCP_SERVERS": "{}",
    "OPENAI_API_KEY": "docs-example",
    "OPENAI_MODEL": "fake-docs-example",
    "OPENAI_BASE_URL": "",
}


def _tokens(meta: str) -> list[str]:
    """Meta words, with any ``reason="..."`` blanked first so a space inside the quoted
    reason can't be mistaken for a second token (e.g. reason="needs a live server to run").
    """
    return REASON.sub("", meta).split()


@cache
def _fences_of(page: Path) -> tuple[list[tuple[int, str]], list[tuple[str, str]]]:
    """``(run fences, file fences)`` for one page: run as ``(fence-index, source)``, file as
    ``(relative-path, source)`` — both in the order they appear on the page.
    """
    runs: list[tuple[int, str]] = []
    files: list[tuple[str, str]] = []
    for i, (_lang, meta, src) in enumerate(FENCE.findall(page.read_text())):
        tokens = _tokens(meta)
        file_token = next((t for t in tokens if t.startswith("file=")), None)
        if file_token is not None:
            files.append((file_token.removeprefix("file="), src))
        elif "run" in tokens:
            runs.append((i, src))
    return runs, files


def _run_cases() -> list[tuple[Path, int, str]]:
    return [(page, i, src) for page in _pages() for i, src in _fences_of(page)[0]]


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


@pytest.mark.parametrize("page,index,src", RUN_CASES, ids=[f"{page.name}::run#{i}" for page, i, _ in RUN_CASES])
def test_run_fence_executes(page: Path, index: int, src: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, files = _fences_of(page)
    for relpath, file_src in files:
        _write_file(tmp_path, page, relpath, file_src)
    for key, value in _PINNED_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr("agentdeck.agents.runners.base.OpenAIProvider", provider_of(ScriptedModel(deltas=("hi",))))
    monkeypatch.chdir(tmp_path)
    # the project alias is process-global; drop stale mounts from a previous fence/page
    for mod in [m for m in sys.modules if m.startswith("agentdeck_project")]:
        del sys.modules[mod]
    reset_settings_cache()
    try:
        # the compile filename is what makes a traceback name the page and fence
        exec(compile(src, f"<{page.name}:run#{index}>", "exec"), {"__name__": "__main__"})
    except Exception as e:
        raise AssertionError(f"{page.name} run fence #{index}: {e}") from e


@pytest.mark.parametrize("page,meta", ILLUSTRATIVE_CASES, ids=[p.name for p, _ in ILLUSTRATIVE_CASES])
def test_illustrative_fences_have_reason(page: Path, meta: str) -> None:
    assert REASON.search(meta), f'{page.name}: illustrative needs reason="why this cannot run"'


__all__ = ["ILLUSTRATIVE_CASES", "RUN_CASES"]
