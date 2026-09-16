"""Black-box CLI-contract tests (D4 exit codes, `config set` validation).

Written from the spec only: exit codes 0 solved / 2 candidate / 3 exhausted /
4 sandbox / 5 provider / 6 usage-config, and `runectl config set` accepting
or rejecting known/unknown keys and values. No Docker, no network, no real
provider keys — every case here is a usage/config error or a read-only
listing, so it never reaches the sandbox or a provider.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def cli_env(tmp_path: Path) -> dict[str, str]:
    """Isolated RUNECTL_HOME/RUNECTL_CONFIG_HOME — never the developer's real ones."""
    return {
        **os.environ,
        "RUNECTL_HOME": str(tmp_path / "home"),
        "RUNECTL_CONFIG_HOME": str(tmp_path / "config"),
    }


def _run(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "runectl", *args], capture_output=True, text=True, env=env
    )


# -- Criterion 1: exit codes -------------------------------------------------


def test_run_without_model_exits_usage_error_not_candidate(cli_env: dict[str, str]) -> None:
    """`run` with no `--model` is a usage error (6), not a `candidate` outcome (2)."""
    result = _run(["run", "--name", "x", "--category", "misc"], cli_env)
    assert result.returncode == 6, (result.returncode, result.stdout, result.stderr)


def test_run_with_unknown_model_exits_usage_error(cli_env: dict[str, str]) -> None:
    """An unresolvable `--model` value is a usage error (6)."""
    result = _run(
        ["run", "--model", "no-such-model", "--name", "x", "--category", "misc"], cli_env
    )
    assert result.returncode == 6, (result.returncode, result.stdout, result.stderr)


def test_models_list_exits_zero(cli_env: dict[str, str]) -> None:
    """`models list` is a read-only listing — no key, no network, exit 0."""
    result = _run(["models", "list"], cli_env)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)


def test_runs_list_exits_zero(cli_env: dict[str, str]) -> None:
    """`runs list` is a legitimate empty state before any run exists (D3) — exit 0."""
    result = _run(["runs", "list"], cli_env)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)


def test_version_flag_exits_zero(cli_env: dict[str, str]) -> None:
    result = _run(["--version"], cli_env)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)


# -- Criterion 8: `config set` validation ------------------------------------


def test_config_set_invalid_approval_value_exits_usage_error(cli_env: dict[str, str]) -> None:
    result = _run(["config", "set", "run.approval", "yolo"], cli_env)
    assert result.returncode == 6, (result.returncode, result.stdout, result.stderr)


def test_config_set_valid_approval_value_exits_zero(cli_env: dict[str, str]) -> None:
    result = _run(["config", "set", "run.approval", "strict"], cli_env)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)


def test_config_set_unknown_section_exits_usage_error(cli_env: dict[str, str]) -> None:
    result = _run(["config", "set", "bogus.section", "x"], cli_env)
    assert result.returncode == 6, (result.returncode, result.stdout, result.stderr)
