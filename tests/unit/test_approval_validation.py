"""`--approval` is validated against D11's closed set, loudly.

The regression: the flag went through as an unvalidated string, and `FlagJudge`
branched on `auto` and `strict` with *everything else* falling through to
`gated`. So `--approval strcit` ran the whole challenge under a policy the user
never asked for and was never told about.

The direction is what makes it worth a test rather than a shrug. `gated` is the
weakest of the three from the false-flag subsystem's point of view — the only
one that can auto-finalize on the deterministic checks, where `strict`
finalizes nothing. A typo silently *relaxing* the flag gate is the wrong way
for a flag to fail.

Fixed 2026-09-10; documented as an unscheduled gap in docs/STATUS.md before that.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from runectl.cli import bench_cmd
from runectl.cli.app import app
from runectl.cli.run_cmd import parse_approval
from runectl.errors import UsageError
from runectl.flags.judge import APPROVAL_POLICIES

runner = CliRunner()


@pytest.mark.parametrize("policy", APPROVAL_POLICIES)
def test_every_documented_policy_is_accepted(policy: str) -> None:
    assert parse_approval(policy) == policy


def test_the_closed_set_is_exactly_d11s_three() -> None:
    """A guard on the vocabulary itself: adding a fourth policy without a dated
    D11 amendment should break here rather than pass quietly."""
    assert APPROVAL_POLICIES == ("gated", "strict", "auto")


@pytest.mark.parametrize("bad", ["strcit", "GATED", "", "yes", "none", "gated "])
def test_anything_else_is_a_usage_error(bad: str) -> None:
    with pytest.raises(UsageError) as caught:
        parse_approval(bad)
    assert caught.value.exit_code == 6
    # The message names what was rejected and what was allowed, so the typo is
    # fixable from the error alone.
    assert repr(bad) in str(caught.value)
    assert "gated, strict, auto" in str(caught.value)


def test_a_typo_no_longer_degrades_into_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    """The regression proper, at the CLI boundary: `strcit` must not run.

    Asserted by counting runs, not just the exit code — the bug was that the
    challenge ran happily, under the wrong policy. Zero runs is the fix.
    """
    calls = {"n": 0}

    def boom(challenge, request, store=None):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        raise AssertionError("a run started under an unvalidated --approval")

    monkeypatch.setattr(bench_cmd, "execute_run", boom)
    result = runner.invoke(
        app,
        ["bench", "run", "--suite", "bench/practice", "--model", "claude-sonnet-5",
         "--approval", "strcit", "--output", "json"],
    )
    assert result.exit_code == 6
    assert calls["n"] == 0
    assert "strcit" in result.output


def test_bench_rejects_it_before_loading_the_suite() -> None:
    """A suite is unattended and long; the typo has to surface before spend, not
    after the third challenge. A nonexistent suite path would also exit 6, so
    this points at a real suite and relies on the flag being checked first."""
    result = runner.invoke(
        app,
        ["bench", "run", "--suite", "bench/practice", "--model", "claude-sonnet-5",
         "--approval", "strcit", "--dry-run"],
    )
    assert result.exit_code == 6
    assert "--approval" in result.output
