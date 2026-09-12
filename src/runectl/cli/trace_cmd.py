"""`runectl trace show`. ``timeline`` is the primary review surface;
``jsonl`` is the raw event stream."""

from __future__ import annotations

import typer

from runectl.cli.render import _width, event_line
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

    # Same renderer as a live run (render.py), so what you read afterwards is
    # exactly what you would have watched happen.
    typer.echo(f"run {manifest.run_id}")
    for event in events:
        line = event_line(event.payload(), _width())
        if line is not None:
            typer.echo(line)
