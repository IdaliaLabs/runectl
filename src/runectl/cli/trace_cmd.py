"""`runectl trace show` (plan §9.4). ``timeline`` is the primary review surface;
``jsonl`` is the raw event stream."""

from __future__ import annotations

import typer

from runectl.trace.reader import load_run
from runectl.trace.store import Store

app = typer.Typer(add_completion=False, help="Inspect a run's trace.")


@app.command("show")
def show(
    run_id: str,
    format: str = typer.Option("timeline", "--format", help="timeline | jsonl"),
) -> None:
    store = Store()
    try:
        manifest, events = load_run(run_id, store=store)
    except FileNotFoundError as exc:
        typer.echo(f"no such run: {run_id}", err=True)
        raise typer.Exit(code=6) from exc

    if format == "jsonl":
        for event in events:
            typer.echo(event.model_dump_json())
        return

    typer.echo(
        f"run {manifest.run_id}  {manifest.challenge_name} [{manifest.category}]  model={manifest.model}"
    )
    for event in events:
        typer.echo(f"  [{event.seq:>4}] {event.type:<18} {event.payload()}")
    if manifest.outcome is not None:
        typer.echo(
            f"outcome={manifest.outcome} exit_code={manifest.exit_code} cost=${manifest.cost_usd:.4f} "
            f"steps={manifest.steps_used}"
        )
