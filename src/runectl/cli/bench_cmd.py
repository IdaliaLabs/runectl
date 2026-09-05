"""`runectl bench` — stub wired for M8 (plan §9.1, "What skeleton excludes")."""

from __future__ import annotations

import typer

app = typer.Typer(add_completion=False, help="Run the capability benchmark suite (lands in M8).")


@app.command("run")
def bench_run(suite: str = typer.Option("bench/practice", "--suite")) -> None:
    typer.echo("`runectl bench` lands in M8 (bench + capability report) — not implemented yet.", err=True)
    raise typer.Exit(code=6)
