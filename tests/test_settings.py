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

AGENTDECK_PKG = Path(__file__).resolve().parents[1] / "agentdeck"
_SUBPROCESS_TIMEOUT = 30

_PROBE = "from agentdeck.runtime.settings import get_settings; print(get_settings().openai.model)"


def _fake_site_packages_install(root: Path) -> Path:
    """Copy the `agentdeck` package tree under `root`, mimicking a real (non-editable)
    pip install into a venv's `site-packages` — deliberately far from any project dir.
    """
    site_packages = root / "venv" / "lib" / "python3.x" / "site-packages"
    shutil.copytree(AGENTDECK_PKG, site_packages / "agentdeck", ignore=shutil.ignore_patterns("__pycache__"))
    return site_packages


def _run_probe(cwd: Path, site_packages: Path, extra_env: dict[str, str] | None = None) -> str:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(site_packages)
    for key in ("OPENAI_MODEL", "OPENAI_API_KEY", "OPENAI_BASE_URL", "APP_CONFIG_PATH"):
        env.pop(key, None)
    env.update(extra_env or {})
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=_SUBPROCESS_TIMEOUT,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


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
