"""`runectl bench run` — score the practice suite (M8, plan §10.1).

Drives real runs through `run_cmd.execute_run`, the same path `runectl run`
uses, then scores each against its `expected.json`. Reports the solve rate and
D16's progress-waste ratio together, because a high solve rate bought with
brute force is not the thing being aimed at.

Spending is bounded twice on purpose: `--max-cost` caps one run, `--max-total-cost`
caps the suite and stops it cleanly mid-way rather than after the fact. An
overnight bench that can only cost what you said it can cost is the difference
between running one and not.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from runectl.bench.suite import (
    BenchReport,
    CaseResult,
    SuiteError,
    load_suite,
    render_report,
    score,
)
from runectl.cli.run_cmd import RunRequest, challenge_from_file, execute_run, parse_thinking
from runectl.config import DEFAULT_MAX_COST_USD
from runectl.errors import ProviderError, RunectlError, SandboxError, UsageError

app = typer.Typer(add_completion=False, help="Run the capability benchmark suite (D16, plan §10.1).")


@app.command("run")
def bench_run(
    suite: str = typer.Option("bench/practice", "--suite"),
    model: str = typer.Option(..., "--model"),
    utility_model: str | None = typer.Option(None, "--utility-model"),
    api_key: str | None = typer.Option(None, "--api-key"),
    approval: str = typer.Option("gated", "--approval"),
    only: list[str] = typer.Option([], "--only", help="Challenge name or directory; repeatable"),
    max_steps: int | None = typer.Option(None, "--max-steps"),
    max_cost: float = typer.Option(DEFAULT_MAX_COST_USD, "--max-cost", help="USD ceiling per run"),
    max_total_cost: float = typer.Option(
        0.0, "--max-total-cost", help="USD ceiling for the whole suite; 0 disables it"
    ),
    record: bool = typer.Option(False, "--record"),
    report_path: str | None = typer.Option(None, "--report", help="Write the JSON report here too"),
    output: str = typer.Option("human", "--output", help="human | json"),
    dry_run: bool = typer.Option(False, "--dry-run", help="List what would run, spend nothing"),
    thinking: str = typer.Option(
        "off",
        "--thinking",
        help="off|low|medium|high|xhigh|max (D20). Defaults to off, not the "
        "configured per-provider default — a suite runs unattended and repeatably.",
    ),
) -> None:
    """Run every challenge in the suite and score it. Exits 1 on any false flag."""
    try:
        resolved_thinking = parse_thinking(thinking)
    except UsageError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=6) from exc

    try:
        cases = load_suite(Path(suite), only=only)
    except SuiteError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=6) from exc

    if dry_run:
        for case in cases:
            typer.echo(f"{case.name}\t{case.category}\t{case.challenge_path}")
        return

    results: list[CaseResult] = []
    spent = 0.0
    stopped_early = ""
    for case in cases:
        if max_total_cost > 0 and spent >= max_total_cost:
            stopped_early = (
                f"stopped after {len(results)}/{len(cases)} challenges: "
                f"${spent:.4f} of the ${max_total_cost:.2f} suite ceiling is spent"
            )
            break
        challenge = challenge_from_file(case.challenge_path)
        request = RunRequest(
            model=model,
            utility_model=utility_model,
            api_key=api_key,
            approval=approval,
            max_steps=max_steps,
            # Never let one run overshoot what is left of the suite ceiling.
            max_cost=min(max_cost, max_total_cost - spent) if max_total_cost > 0 else max_cost,
            record=record,
            output="jsonl" if output == "json" else "human",
            thinking=resolved_thinking,
        )
        try:
            result = execute_run(challenge, request)
        except UsageError as exc:
            # A usage/config error (an invalid API key, an unknown model) is not
            # challenge-specific — it will fail every run in the suite identically.
            # Abort the whole bench cleanly with exit 6 rather than scoring ten
            # copies of the same misconfiguration.
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=6) from exc
        except (SandboxError, ProviderError) as exc:
            # One broken challenge does not end the suite; it is scored as an
            # error and the report says so.
            results.append(
                score(
                    case, run_id=None, outcome="error", flag=None,
                    exit_code=exc.exit_code if isinstance(exc, RunectlError) else 6,
                    steps_used=0, progress_steps=0, blocked_steps=0, cost_usd=0.0,
                )
            )
            typer.echo(f"{case.name}: {exc}", err=True)
            continue
        outcome = result.outcome
        spent += outcome.cost_usd
        results.append(
            score(
                case,
                run_id=result.run_id,
                outcome=outcome.outcome,
                flag=outcome.flag,
                exit_code=outcome.exit_code,
                steps_used=outcome.steps_used,
                progress_steps=outcome.progress_steps,
                blocked_steps=outcome.blocked_steps,
                cost_usd=outcome.cost_usd,
            )
        )

    report = BenchReport(suite=suite, model=model, results=tuple(results))
    payload = report.as_dict()
    if stopped_early:
        payload["stopped_early"] = stopped_early

    if report_path:
        Path(report_path).write_text(json.dumps(payload, indent=2) + "\n")

    if output == "json":
        typer.echo(json.dumps(payload))
    else:
        typer.echo(render_report(report))
        if stopped_early:
            typer.echo(f"\n  {stopped_early}")

    # A false flag is the one result that is worse than failing, so it is the
    # one that makes the command itself fail (D15).
    if report.false_flags:
        raise typer.Exit(code=1)
