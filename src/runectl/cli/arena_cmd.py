"""`runectl arena build|status` (D2, plan §9.1)."""

from __future__ import annotations

import typer

from runectl.errors import SandboxError
from runectl.sandbox.arena_build import build as build_arena
from runectl.sandbox.arena_build import status as arena_status

app = typer.Typer(add_completion=False, help="Manage the arena sandbox image.")


@app.command("build")
def build_cmd() -> None:
    code = build_arena()
    raise typer.Exit(code=code)


@app.command("status")
def status_cmd() -> None:
    try:
        image_id = arena_status()
    except SandboxError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=exc.exit_code) from exc
    if image_id is None:
        typer.echo("arena image not built — run `runectl arena build`", err=True)
        raise typer.Exit(code=4)
    typer.echo(f"runectl/arena:kali -> {image_id}")
