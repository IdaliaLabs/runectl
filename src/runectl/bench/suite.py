"""The capability benchmark (M8, plan §10.1) — what earns the right to tune D8.

Two things this scores, and the second is the one that is easy to leave out:

- **Solve rate**, against each challenge's `expected.json`.
- **Step waste** — D16's framing. A run is not failing because it took many
  steps; it is failing because it took steps that produced no new signal. The
  report carries `progress_steps / steps_used` alongside the solve rate, because
  optimizing solve rate alone is how you end up with a tool that brute-forces.

And one thing it refuses to average away: a **false flag**. The V1 gate is 2 of
5 solved with *zero* of them, so a wrong flag confidently finalized is not
0.8 of a solve, it is a failure of the thing the tool exists to get right.

D10 (no answer keys anywhere near the solver): `expected.json` is read only in
`score()`, which takes a finished outcome and never touches the run. Nothing in
`loop/`, `flags/` or `progress/` imports this module.
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

CaseStatus = str  # "solved" | "false_flag" | "candidate" | "unsolved" | "error"


class SuiteError(Exception):
    """A malformed suite directory — a usage error, not a bench result."""


@dataclass(frozen=True)
class BenchCase:
    name: str
    challenge_path: Path
    category: str
    expected_flag: str

    @property
    def directory(self) -> Path:
        return self.challenge_path.parent


@dataclass(frozen=True)
class CaseResult:
    """One scored run. `flag` is what the tool produced, not what was expected."""

    name: str
    category: str
    status: CaseStatus
    run_id: str | None
    flag: str | None
    exit_code: int
    steps_used: int
    progress_steps: int
    blocked_steps: int
    cost_usd: float
    detail: str = ""

    @property
    def progress_ratio(self) -> float:
        return self.progress_steps / self.steps_used if self.steps_used else 0.0

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "category": self.category,
            "status": self.status,
            "run_id": self.run_id,
            "flag": self.flag,
            "exit_code": self.exit_code,
            "steps_used": self.steps_used,
            "progress_steps": self.progress_steps,
            "blocked_steps": self.blocked_steps,
            "progress_ratio": round(self.progress_ratio, 4),
            "cost_usd": round(self.cost_usd, 6),
            "detail": self.detail,
        }


@dataclass(frozen=True)
class BenchReport:
    suite: str
    model: str
    results: tuple[CaseResult, ...] = field(default_factory=tuple)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def solved(self) -> int:
        return sum(1 for r in self.results if r.status == "solved")

    @property
    def false_flags(self) -> int:
        return sum(1 for r in self.results if r.status == "false_flag")

    @property
    def solve_rate(self) -> float:
        return self.solved / self.total if self.total else 0.0

    @property
    def steps_used(self) -> int:
        return sum(r.steps_used for r in self.results)

    @property
    def progress_steps(self) -> int:
        return sum(r.progress_steps for r in self.results)

    @property
    def blocked_steps(self) -> int:
        return sum(r.blocked_steps for r in self.results)

    @property
    def progress_ratio(self) -> float:
        """D16's primary metric, across the whole suite."""
        return self.progress_steps / self.steps_used if self.steps_used else 0.0

    @property
    def waste_ratio(self) -> float:
        return 1.0 - self.progress_ratio if self.steps_used else 0.0

    @property
    def cost_usd(self) -> float:
        return sum(r.cost_usd for r in self.results)

    @property
    def gate_met(self) -> bool:
        """The V1 gate (DECISIONS.md D15, plan §10.1): 2 of 5 solved, 0 false flags."""
        return self.solved >= 2 and self.false_flags == 0

    def as_dict(self) -> dict[str, object]:
        return {
            "suite": self.suite,
            "model": self.model,
            "total": self.total,
            "solved": self.solved,
            "false_flags": self.false_flags,
            "solve_rate": round(self.solve_rate, 4),
            "steps_used": self.steps_used,
            "progress_steps": self.progress_steps,
            "blocked_steps": self.blocked_steps,
            "progress_ratio": round(self.progress_ratio, 4),
            "waste_ratio": round(self.waste_ratio, 4),
            "cost_usd": round(self.cost_usd, 6),
            "gate_met": self.gate_met,
            "cases": [r.as_dict() for r in self.results],
        }


def load_suite(root: Path, *, only: Sequence[str] = ()) -> list[BenchCase]:
    """Every challenge directory under `root` holding a `chal.toml` + `expected.json`."""
    if not root.is_dir():
        raise SuiteError(f"no such suite directory: {root}")
    cases: list[BenchCase] = []
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        challenge_path = directory / "chal.toml"
        expected_path = directory / "expected.json"
        if not challenge_path.exists():
            continue
        if not expected_path.exists():
            raise SuiteError(f"{directory.name} has a chal.toml but no expected.json to score against")
        raw = tomllib.loads(challenge_path.read_text())
        expected = json.loads(expected_path.read_text())
        flag = expected.get("flag")
        if not isinstance(flag, str) or not flag:
            raise SuiteError(f"{directory.name}/expected.json has no flag to score against")
        cases.append(
            BenchCase(
                name=raw.get("name", directory.name),
                challenge_path=challenge_path,
                category=raw.get("category", ""),
                expected_flag=flag,
            )
        )
    if only:
        wanted = set(only)
        cases = [c for c in cases if c.name in wanted or c.directory.name in wanted]
        missing = wanted - {c.name for c in cases} - {c.directory.name for c in cases}
        if missing:
            raise SuiteError(f"no such challenge in {root}: {', '.join(sorted(missing))}")
    if not cases:
        raise SuiteError(f"{root} contains no challenges")
    return cases


def score(
    case: BenchCase,
    *,
    run_id: str | None,
    outcome: str,
    flag: str | None,
    exit_code: int,
    steps_used: int,
    progress_steps: int,
    blocked_steps: int,
    cost_usd: float,
) -> CaseResult:
    """Turn one finished run into a scored result.

    The distinction that matters: a run that *finalized* the wrong string is a
    false flag, while one that *held* it for approval is not — holding a wrong
    candidate is the false-flag subsystem working, and scoring it the same as
    submitting one would punish the tool for being careful.
    """
    correct = flag is not None and flag == case.expected_flag
    if outcome == "solved":
        status = "solved" if correct else "false_flag"
        detail = "" if correct else f"finalized {flag!r}, expected {case.expected_flag!r}"
    elif outcome == "candidate":
        status = "candidate"
        detail = (
            "held the correct flag for approval"
            if correct
            else f"held {flag!r} for approval, expected {case.expected_flag!r}"
        )
    elif outcome == "error":
        status, detail = "error", "the run could not complete"
    else:
        status, detail = "unsolved", "no candidate found"

    return CaseResult(
        name=case.name,
        category=case.category,
        status=status,
        run_id=run_id,
        flag=flag,
        exit_code=exit_code,
        steps_used=steps_used,
        progress_steps=progress_steps,
        blocked_steps=blocked_steps,
        cost_usd=cost_usd,
        detail=detail,
    )


def render_report(report: BenchReport) -> str:
    """The human view. One line per case, then the two numbers that matter."""
    mark = {
        "solved": "SOLVED  ",
        "false_flag": "FALSE!  ",
        "candidate": "HELD    ",
        "unsolved": "unsolved",
        "error": "error   ",
    }
    lines = [f"suite {report.suite}  model {report.model}", ""]
    for result in report.results:
        line = (
            f"  {mark.get(result.status, result.status)}  {result.name:<28}"
            f"  {result.progress_steps}/{result.steps_used} steps"
            f"  ${result.cost_usd:.4f}"
        )
        if result.detail:
            line += f"\n      {result.detail}"
        lines.append(line)
    lines += [
        "",
        f"  solve rate     {report.solved}/{report.total}  ({report.solve_rate:.0%})",
        f"  false flags    {report.false_flags}",
        f"  progress ratio {report.progress_ratio:.2f}  (waste {report.waste_ratio:.2f})",
        f"  blocked steps  {report.blocked_steps}",
        f"  cost           ${report.cost_usd:.4f}",
        "",
        f"  V1 gate (2 solved, 0 false flags): {'MET' if report.gate_met else 'not met'}",
    ]
    return "\n".join(lines)
