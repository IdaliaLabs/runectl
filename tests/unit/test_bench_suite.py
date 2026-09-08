"""Suite loading, scoring, and the two numbers the report exists to carry."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from runectl.bench.suite import BenchCase, BenchReport, SuiteError, load_suite, render_report, score

FLAG = "csictf{r34l_fl4g}"


def _case(tmp_path: Path, name: str, flag: str = FLAG) -> BenchCase:
    directory = tmp_path / name
    directory.mkdir(exist_ok=True)
    (directory / "chal.toml").write_text(f'name = "{name}"\ncategory = "crypto"\n')
    (directory / "expected.json").write_text(json.dumps({"flag": flag}))
    return BenchCase(
        name=name, challenge_path=directory / "chal.toml", category="crypto", expected_flag=flag
    )


def _scored(case: BenchCase, outcome: str, flag: str | None, **kwargs: int | float):
    defaults: dict[str, object] = {
        "run_id": "r1", "exit_code": 0, "steps_used": 10,
        "progress_steps": 4, "blocked_steps": 1, "cost_usd": 0.02,
    }
    defaults.update(kwargs)
    return score(case, outcome=outcome, flag=flag, **defaults)  # type: ignore[arg-type]


def test_the_right_flag_is_a_solve(tmp_path: Path) -> None:
    result = _scored(_case(tmp_path, "a"), "solved", FLAG)
    assert result.status == "solved"
    assert result.progress_ratio == 0.4


def test_a_finalized_wrong_flag_is_a_false_flag(tmp_path: Path) -> None:
    result = _scored(_case(tmp_path, "a"), "solved", "csictf{wrong}")
    assert result.status == "false_flag"
    assert "expected" in result.detail


def test_a_held_wrong_flag_is_not_a_false_flag(tmp_path: Path) -> None:
    """Holding a wrong candidate is the subsystem working, not a failure."""
    result = _scored(_case(tmp_path, "a"), "candidate", "csictf{wrong}")
    assert result.status == "candidate"


def test_an_unsolved_run_is_not_a_false_flag(tmp_path: Path) -> None:
    assert _scored(_case(tmp_path, "a"), "exhausted", None).status == "unsolved"
    assert _scored(_case(tmp_path, "a"), "error", None).status == "error"


def test_the_report_aggregates_waste_across_the_suite(tmp_path: Path) -> None:
    results = (
        _scored(_case(tmp_path, "a"), "solved", FLAG, steps_used=10, progress_steps=5),
        _scored(_case(tmp_path, "b"), "exhausted", None, steps_used=30, progress_steps=3),
    )
    report = BenchReport(suite="s", model="m", results=results)
    assert report.solved == 1
    assert report.solve_rate == 0.5
    # D16: the ratio is over the whole suite's steps, not a mean of per-run ratios.
    assert report.progress_ratio == 8 / 40
    assert report.waste_ratio == 1 - 8 / 40
    assert report.blocked_steps == 2


def test_the_v1_gate_needs_two_solves_and_zero_false_flags(tmp_path: Path) -> None:
    solved = [_scored(_case(tmp_path, f"s{i}"), "solved", FLAG) for i in range(2)]
    assert BenchReport(suite="s", model="m", results=tuple(solved)).gate_met

    with_false = [*solved, _scored(_case(tmp_path, "bad"), "solved", "csictf{wrong}")]
    assert not BenchReport(suite="s", model="m", results=tuple(with_false)).gate_met


def test_a_challenge_without_expected_json_is_a_usage_error(tmp_path: Path) -> None:
    directory = tmp_path / "broken"
    directory.mkdir()
    (directory / "chal.toml").write_text('name = "broken"\ncategory = "misc"\n')
    with pytest.raises(SuiteError, match="expected.json"):
        load_suite(tmp_path)


def test_only_selects_by_name_or_directory(tmp_path: Path) -> None:
    _case(tmp_path, "alpha")
    _case(tmp_path, "beta")
    assert [c.name for c in load_suite(tmp_path, only=["beta"])] == ["beta"]
    with pytest.raises(SuiteError, match="no such challenge"):
        load_suite(tmp_path, only=["gamma"])


def test_the_human_report_names_the_gate_and_the_waste(tmp_path: Path) -> None:
    report = BenchReport(
        suite="s", model="m", results=(_scored(_case(tmp_path, "a"), "solved", FLAG),)
    )
    text = render_report(report)
    assert "solve rate" in text
    assert "progress ratio" in text
    assert "V1 gate" in text
