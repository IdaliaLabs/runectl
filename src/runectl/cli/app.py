"""The top-level Typer app (D4 V1 command surface, plan §9.1, §9.5).

Only this package renders (D13) — core code emits events and never prints.
Exit codes come from D4's table plus `errors.py`'s hierarchy; expected
failures are one-line stderr messages, never a raw traceback.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer

from runectl.categories.loader import CategoryLoadError, CategoryNotFoundError
from runectl.categories.loader import load as load_category
from runectl.cli import arena_cmd, bench_cmd, flag_cmd, keys_cmd, run_cmd, trace_cmd
from runectl.flags.review import ReviewVerdict, replay_reviewer
from runectl.loop.runner import Runner
from runectl.loop.state import Challenge
from runectl.providers.registry import UnknownModelError
from runectl.providers.registry import resolve as resolve_model
from runectl.providers.replay import ReplayProvider
from runectl.sandbox.base import sandbox_session
from runectl.sandbox.replay import ReplaySandbox, exec_results_from_trace
from runectl.trace.events import FlagReviewed, ToolCall, TriageResult
from runectl.trace.index import IndexDB
from runectl.trace.reader import TraceReader
from runectl.trace.store import Store

app = typer.Typer(add_completion=False, help="runectl — an agentic CTF solver CLI (Idalia Labs).")
app.command("run")(run_cmd.run_command)
app.add_typer(keys_cmd.app, name="keys")
app.add_typer(arena_cmd.app, name="arena")
app.add_typer(trace_cmd.app, name="trace")
app.add_typer(flag_cmd.app, name="flag")
app.add_typer(bench_cmd.app, name="bench")

index_app = typer.Typer(
    add_completion=False, help="Maintain the derived SQLite index (D3 — never authoritative)."
)


@index_app.command("rebuild")
def index_rebuild() -> None:
    store = Store()
    count = IndexDB(store.home / "index.db").rebuild(store)
    typer.echo(f"rebuilt index from {count} run(s)")


app.add_typer(index_app, name="index")


def _tool_call_sequence(trace_path: Path, artifacts_dir: Path) -> list[tuple[str, dict[str, Any]]]:
    sequence: list[tuple[str, dict[str, Any]]] = []
    for event in TraceReader(trace_path, artifacts_dir):
        payload = event.payload()
        if isinstance(payload, ToolCall):
            sequence.append((payload.tool, payload.arguments))
    return sequence


@app.command("replay")
def replay_command(run_id: str, check: bool = typer.Option(False, "--check")) -> None:
    """Re-run a recorded run from its cassette — no daemon, no spend (D2, D5)."""
    store = Store()
    try:
        manifest = store.read_manifest(run_id)
    except FileNotFoundError as exc:
        typer.echo(f"no such run: {run_id}", err=True)
        raise typer.Exit(code=6) from exc

    cassette_path = store.cassette_path(run_id)
    if not cassette_path.exists():
        typer.echo(f"run {run_id} has no cassette — it wasn't run with --record", err=True)
        raise typer.Exit(code=6)

    challenge_data = manifest.config_snapshot.get("challenge")
    if not isinstance(challenge_data, dict):
        typer.echo(f"run {run_id}'s manifest has no recorded challenge to replay", err=True)
        raise typer.Exit(code=6)
    chal = Challenge.model_validate(challenge_data)

    try:
        category_data = load_category(manifest.category)
    except (CategoryNotFoundError, CategoryLoadError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=6) from exc

    try:
        model_info = resolve_model(manifest.model)
    except UnknownModelError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=6) from exc

    # A replay contacts no API, so it must not report spend. The ledger prices
    # whatever the provider returns, and ReplayProvider returns the *recorded*
    # usage — which would otherwise be re-billed into run.json and make a replay
    # look like it cost what the original run cost. Zero-priced model, zero
    # reported cost; "replay at zero spend" has to be true in the manifest too.
    model_info = model_info.model_copy(update={"price_in": 0.0, "price_out": 0.0})
    provider = ReplayProvider(cassette_path)
    exec_results = exec_results_from_trace(store.trace_path(run_id), store.artifacts_dir(run_id))
    sandbox = ReplaySandbox(exec_results)
    approval_policy = str(manifest.config_snapshot.get("approval_policy", "gated"))

    triage_override = None
    recorded_reviews: list[ReviewVerdict] = []
    for event in TraceReader(store.trace_path(run_id), store.artifacts_dir(run_id)):
        payload = event.payload()
        if isinstance(payload, TriageResult) and triage_override is None:
            triage_override = payload
        elif isinstance(payload, FlagReviewed):
            recorded_reviews.append(ReviewVerdict(payload.sound, payload.reason))

    replay_run_id, writer = store.new_run(
        challenge_name=chal.name,
        category=chal.category,
        model=model_info.id,
        provider=model_info.provider,
        config_snapshot={"replay_of": run_id},
    )
    runner = Runner(
        challenge=chal,
        category=category_data,
        model=model_info,
        provider=provider,
        sandbox=sandbox,
        writer=writer,
        approval_policy=approval_policy,
        triage_override=triage_override,
        reviewer=replay_reviewer(recorded_reviews),
    )
    with sandbox_session(sandbox):
        outcome = runner.run()
    writer.close()
    store.finish_run(
        replay_run_id,
        outcome=outcome.outcome,
        exit_code=outcome.exit_code,
        flag=outcome.flag,
        cost_usd=outcome.cost_usd,
        steps_used=outcome.steps_used,
        progress_steps=outcome.progress_steps,
        blocked_steps=outcome.blocked_steps,
    )

    typer.echo(replay_run_id)
    if check:
        original = _tool_call_sequence(store.trace_path(run_id), store.artifacts_dir(run_id))
        replayed = _tool_call_sequence(store.trace_path(replay_run_id), store.artifacts_dir(replay_run_id))
        if original != replayed:
            typer.echo(f"MISMATCH: {replay_run_id} diverged from {run_id}'s tool-call sequence", err=True)
            raise typer.Exit(code=1)
        typer.echo(f"OK: {replay_run_id} reproduces {run_id}'s tool-call sequence at zero spend")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
