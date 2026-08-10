"""Regression tests for issue #16: `.env`/`config.yaml` must resolve from the project's
`Path.cwd()` — matching how `mount_project_dir` locates `./.agentdeck` — never from
wherever the `agentdeck` package itself is installed. The difference is invisible in a
repo checkout (package and project share a root) and only bites once `agentdeck` is
pip-installed into `site-packages`, away from any project directory: exactly the shape
these tests reproduce.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agentdeck.runtime.settings import get_settings, reset_settings_cache

AGENTDECK_PKG = Path(__file__).resolve().parents[1] / "agentdeck"
_SUBPROCESS_TIMEOUT = 30

_PROBE = (
    "import agentdeck; from agentdeck.runtime.settings import get_settings; "
    "print(agentdeck.__file__); print(get_settings().openai.model)"
)


def _fake_site_packages_install(root: Path) -> Path:
    """Copy the `agentdeck` package tree under `root`, mimicking a real (non-editable)
    pip install into a venv's `site-packages` — deliberately far from any project dir.
    """
    site_packages = root / "venv" / "lib" / "python3.x" / "site-packages"
    shutil.copytree(AGENTDECK_PKG, site_packages / "agentdeck", ignore=shutil.ignore_patterns("__pycache__"))
    return site_packages


def _run(script: str, cwd: Path, site_packages: Path, extra_env: dict[str, str] | None = None) -> str:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(site_packages)
    for key in ("OPENAI_MODEL", "OPENAI_API_KEY", "OPENAI_BASE_URL", "AGENTDECK_CONFIG_PATH"):
        env.pop(key, None)
    env.update(extra_env or {})
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=_SUBPROCESS_TIMEOUT,
    )
    assert result.returncode == 0, result.stderr
    file_line, model_line = result.stdout.strip().splitlines()
    # A strict setuptools editable install puts a `__editable___..._finder` on
    # `sys.meta_path`, which resolves `import agentdeck` before PYTHONPATH is ever
    # consulted — silently defeating the fake install below and passing vacuously.
    assert file_line.startswith(str(site_packages)), (
        f"agentdeck imported from {file_line!r}, not the fake site-packages install at "
        f"{site_packages!r} — this test proves nothing about the real bug."
    )
    return model_line


def _run_probe(cwd: Path, site_packages: Path, extra_env: dict[str, str] | None = None) -> str:
    return _run(_PROBE, cwd, site_packages, extra_env)


def test_dotenv_in_project_cwd_wins_over_a_stray_env_file_inside_site_packages(tmp_path):
    """The project's own `.env` must load even when `agentdeck` is installed as a package.

    A second `.env` sits exactly where the old `REPO_ROOT = Path(__file__).parents[2]`
    logic would have pointed — inside the fake install's site-packages directory — to
    prove the fix does not merely relocate the bug to a different wrong path.
    """
    site_packages = _fake_site_packages_install(tmp_path)
    (site_packages / ".env").write_text("OPENAI_MODEL=from-site-packages\n")

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / ".env").write_text("OPENAI_MODEL=from-project-dotenv\n")

    model = _run_probe(project_dir, site_packages)

    assert model == "from-project-dotenv"


def test_config_yaml_in_project_cwd_wins_over_the_packaged_default(tmp_path):
    """Same bug, same fix, for `config.yaml` (`resolve_config_path` shared the `REPO_ROOT` logic)."""
    site_packages = _fake_site_packages_install(tmp_path)

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "config.yaml").write_text("openai:\n  model: from-project-config-yaml\n")

    model = _run_probe(project_dir, site_packages)

    assert model == "from-project-config-yaml"


def test_real_env_var_still_outranks_the_dotenv_file(tmp_path):
    """Layered settings means a real environment variable outranks the `.env` file — a
    fix that made `.env` start winning over an exported `AGENTDECK_*`/`OPENAI_*` var
    would trade one bug for a worse one.
    """
    site_packages = _fake_site_packages_install(tmp_path)

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / ".env").write_text("OPENAI_MODEL=from-dotenv\n")

    model = _run_probe(project_dir, site_packages, extra_env={"OPENAI_MODEL": "from-real-env"})

    assert model == "from-real-env"


def test_missing_project_dotenv_does_not_fall_back_to_a_file_near_the_package(tmp_path):
    """No second chance: a decoy `.env` sitting exactly where the old `REPO_ROOT` logic
    would have found one must stay ignored when the project itself has none — the
    packaged default wins, not the site-packages file.
    """
    site_packages = _fake_site_packages_install(tmp_path)
    (site_packages / ".env").write_text("OPENAI_MODEL=from-site-packages\n")

    project_dir = tmp_path / "project"
    project_dir.mkdir()

    model = _run_probe(project_dir, site_packages)

    assert model == "gpt-4.1-mini"


def test_settings_resolve_cwd_at_first_use_not_at_import(tmp_path):
    """A `chdir` between `import agentdeck` and the first `get_settings()` call must
    still land on the project — the same failure mode `mount_project_dir` never has,
    since it always runs after any `chdir` a caller does. Binding `.env`'s path once at
    import time (the pre-fix shape) would freeze whatever cwd was current at import,
    a stale "launch directory" the process may since have left behind.
    """
    site_packages = _fake_site_packages_install(tmp_path)

    launch_dir = tmp_path / "launch"
    launch_dir.mkdir()
    (launch_dir / ".env").write_text("OPENAI_MODEL=from-launch-dir\n")

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / ".env").write_text("OPENAI_MODEL=from-project\n")

    script = (
        "import os, agentdeck; from agentdeck.runtime.settings import get_settings; "
        f"os.chdir({str(project_dir)!r}); "
        "print(agentdeck.__file__); print(get_settings().openai.model)"
    )
    model = _run(script, launch_dir, site_packages)

    assert model == "from-project"


def test_agentdeck_config_path_redirects_the_shared_yaml(tmp_path, monkeypatch):
    """`AGENTDECK_CONFIG_PATH` is the one name that redirects `config.yaml` — issue #155."""
    from agentdeck.runtime.settings import resolve_config_path

    redirected = tmp_path / "elsewhere.yaml"
    redirected.write_text("openai:\n  model: from-redirected-path\n")
    monkeypatch.setenv("AGENTDECK_CONFIG_PATH", str(redirected))

    assert resolve_config_path() == redirected


def test_sandbox_env_and_skills_settings_are_gone():
    """Issue #155: sandboxing left v3 in #163 and `SkillExecutor` — `sandbox_env()`'s only
    caller — was deleted in #164, leaving both with zero callers. A deletion, not the
    `AGENTDECK_SKILL_*` rename the issue originally proposed."""
    import agentdeck.runtime.settings as settings_module

    assert not hasattr(settings_module.Settings, "sandbox_env")
    assert not hasattr(settings_module, "SkillsSettings")
    assert "skills" not in settings_module.Settings.model_fields


def test_the_old_app_config_path_name_is_no_longer_read(tmp_path, monkeypatch):
    """`APP_CONFIG_PATH` was unprefixed and generic; #155 renamed it outright — no shim, no
    fallback. Setting only the old name must resolve as if nothing were set at all."""
    from agentdeck.runtime.settings import PACKAGED_DEFAULT_YAML, resolve_config_path

    decoy = tmp_path / "decoy.yaml"
    decoy.write_text("openai:\n  model: from-old-name-that-must-not-be-read\n")
    monkeypatch.setenv("APP_CONFIG_PATH", str(decoy))
    monkeypatch.delenv("AGENTDECK_CONFIG_PATH", raising=False)
    monkeypatch.chdir(tmp_path)

    assert resolve_config_path() == PACKAGED_DEFAULT_YAML


def test_a_retired_v2_env_name_refuses_to_start(monkeypatch):
    """Nothing binds the old names any more, so a deployment that still exports one would fall
    back to the default — and for the three store variables that default is in-process memory,
    i.e. a durable log quietly becoming ephemeral on upgrade."""
    monkeypatch.setenv("AGENTDECK_EVENTS_BACKEND", "postgres")
    reset_settings_cache()

    with pytest.raises(ValueError, match="AGENTDECK_EVENTS_BACKEND is now AGENTDECK_EVENTS"):
        get_settings()


def test_a_retired_name_alongside_its_replacement_is_only_a_leftover(monkeypatch):
    """Once the new variable is set the migration has happened, so a stale name inherited from a
    container environment must not stop a correctly-configured process from booting."""
    monkeypatch.setenv("AGENTDECK_EVENTS_BACKEND", "postgres")
    monkeypatch.setenv("AGENTDECK_EVENTS", "memory://")
    reset_settings_cache()

    get_settings()  # no raise
