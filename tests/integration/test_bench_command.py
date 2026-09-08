"""`runectl bench run` end to end, with the runs faked — zero daemon, zero spend.

What's under test is the command's own contract: that it scores what the runs
returned, keeps spending inside the suite ceiling, and fails the *command* when
a false flag appears. The solving itself is tested elsewhere.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from runectl.cli import bench_cmd
from runectl.cli.app import app
from runectl.cli.run_cmd import RunResult
from runectl.loop.runner import RunOutcome

runner = CliRunner()
SUITE = "bench/practice"

# The real flags, so a faked run can be made to "solve" a case.
EXPECTED = {
    path.parent.name: json.loads(path.read_text())["flag"]
    for path in Path(SUITE).glob("*/expected.json")
}


def _fake_runs(behavior: dict[str, tuple[str, str | None, float]]):
    """Replace the real executor with one that returns scripted outcomes."""

    def fake(challenge, request, store=None):  # type: ignore[no-untyped-def]
        outcome, flag, cost = behavior.get(challenge.name, ("exhausted", None, 0.01))
        return RunResult(
            run_id=f"fake-{challenge.name}",
            outcome=RunOutcome(
                outcome=outcome,  # type: ignore[arg-type]
                flag=flag,
                exit_code={"solved": 0, "candidate": 2}.get(outcome, 3),
                steps_used=10,
                cost_usd=cost,
                progress_steps=4,
                blocked_steps=1,
            ),
        )

    return fake


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    def apply(behavior: dict[str, tuple[str, str | None, float]]) -> None:
        monkeypatch.setattr(bench_cmd, "execute_run", _fake_runs(behavior))

    return apply


def test_it_scores_the_suite_and_reports_the_waste_ratio(patched) -> None:  # type: ignore[no-untyped-def]
    patched(
        {
            "modern-clueless-child": ("solved", EXPECTED["easy-02"], 0.02),
            "machine-fix": ("solved", EXPECTED["easy-04"], 0.03),
            "little-rsa": ("candidate", EXPECTED["easy-05"], 0.01),
        }
    )
    result = runner.invoke(app, ["bench", "run", "--suite", SUITE, "--model", "claude-sonnet-5",
                                 "--output", "json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["total"] == 5
    assert report["solved"] == 2
    assert report["false_flags"] == 0
    assert report["gate_met"] is True
    # D16: 5 runs x 4 progress of 10 steps.
    assert report["progress_ratio"] == 0.4
    assert report["waste_ratio"] == 0.6
    assert [c["status"] for c in report["cases"]].count("candidate") == 1


def test_a_false_flag_fails_the_command(patched) -> None:  # type: ignore[no-untyped-def]
    patched({"quick-math": ("solved", "csictf{not_the_answer}", 0.02)})
    result = runner.invoke(app, ["bench", "run", "--suite", SUITE, "--model", "claude-sonnet-5",
                                 "--output", "json"])
    assert result.exit_code == 1
    report = json.loads(result.stdout)
    assert report["false_flags"] == 1
    assert report["gate_met"] is False


def test_the_suite_ceiling_stops_it_partway(patched, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    patched({name: ("exhausted", None, 0.20) for name in
             ["quick-math", "modern-clueless-child", "rivest-shamir-adleman", "machine-fix", "little-rsa"]})
    report_path = tmp_path / "report.json"
    result = runner.invoke(app, ["bench", "run", "--suite", SUITE, "--model", "claude-sonnet-5",
                                 "--max-total-cost", "0.50", "--output", "json",
                                 "--report", str(report_path)])
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["total"] == 3  # stopped once $0.60 > $0.50
    assert "stopped_early" in report
    assert json.loads(report_path.read_text())["stopped_early"] == report["stopped_early"]


def test_only_runs_one_challenge(patched) -> None:  # type: ignore[no-untyped-def]
    patched({"machine-fix": ("solved", EXPECTED["easy-04"], 0.02)})
    result = runner.invoke(app, ["bench", "run", "--suite", SUITE, "--model", "claude-sonnet-5",
                                 "--only", "easy-04", "--output", "json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["total"] == 1
    assert report["cases"][0]["name"] == "machine-fix"
    assert report["solve_rate"] == 1.0
