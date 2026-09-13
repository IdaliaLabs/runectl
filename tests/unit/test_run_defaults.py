"""Run-wide config defaults (`[run] approval` / `[run] max_cost`) reach the run path.

Wired 2026-09-13. These two keys had been advertised in `runectl config`'s own
`--help` as "run-wide defaults" since Phase 2 while nothing read them: setting
one succeeded, wrote the file, and silently changed nothing. The tests below
are mostly about *precedence*, because that is the part that can quietly
violate D5 — config may fill a gap the command line left, never override it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runectl.cli.run_cmd import resolve_approval, resolve_max_cost
from runectl.config import DEFAULT_MAX_COST_USD, RUNECTL_CONFIG_ENV
from runectl.errors import UsageError
from runectl.user_config import default_approval, default_max_cost


@pytest.fixture
def config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point config.toml at a temp dir — never the developer's real one."""
    monkeypatch.setenv(RUNECTL_CONFIG_ENV, str(tmp_path))
    return tmp_path


def _write(config_home: Path, body: str) -> None:
    (config_home / "config.toml").write_text(body, encoding="utf-8")


def test_unset_config_falls_back_to_the_builtin_defaults(config_home: Path) -> None:
    assert default_approval() is None
    assert default_max_cost() is None
    assert resolve_approval(None) == "gated"
    assert resolve_max_cost(None) == DEFAULT_MAX_COST_USD


def test_config_supplies_the_default_when_no_flag_is_given(config_home: Path) -> None:
    _write(config_home, '[run]\napproval = "strict"\nmax_cost = "3.25"\n')
    assert resolve_approval(None) == "strict"
    assert resolve_max_cost(None) == 3.25


def test_an_explicit_flag_always_beats_config(config_home: Path) -> None:
    """The D5-adjacent guarantee: config never overrides the command line."""
    _write(config_home, '[run]\napproval = "auto"\nmax_cost = "99.0"\n')
    assert resolve_approval("gated") == "gated"
    assert resolve_max_cost(0.5) == 0.5


def test_a_configured_zero_ceiling_survives(config_home: Path) -> None:
    """0 means "no ceiling" (D19), not "unset" — a truthiness check here would
    silently reimpose the $0.50 default on someone who asked for none."""
    _write(config_home, '[run]\nmax_cost = "0"\n')
    assert default_max_cost() == 0.0
    assert resolve_max_cost(None) == 0.0


def test_an_explicit_zero_flag_survives(config_home: Path) -> None:
    _write(config_home, '[run]\nmax_cost = "5"\n')
    assert resolve_max_cost(0.0) == 0.0


def test_a_bad_stored_approval_is_loud_not_permissive(config_home: Path) -> None:
    """A typo must not hand a run a policy it did not ask for. Failing closed
    would be just as wrong as failing open here — the run should stop and say
    so, which is D20's posture applied to D11's policy set."""
    _write(config_home, '[run]\napproval = "yolo"\n')
    with pytest.raises(UsageError) as caught:
        default_approval()
    assert "yolo" in str(caught.value)


@pytest.mark.parametrize("bad", ["abc", "-1"])
def test_a_bad_stored_max_cost_is_loud(config_home: Path, bad: str) -> None:
    _write(config_home, f'[run]\nmax_cost = "{bad}"\n')
    with pytest.raises(UsageError):
        default_max_cost()
