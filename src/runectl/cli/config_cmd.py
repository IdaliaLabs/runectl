"""`runectl config` — per-provider preferences and run-wide defaults (Phase 2, D20).

Reads/writes `~/.config/runectl/config.toml` via `user_config.py`. This is a
convenience/discovery surface only: it never makes `--model` optional on
`runectl run` (D5 stays intact — see `user_config.py`'s module docstring).
"""

from __future__ import annotations

import typer

from runectl.errors import UsageError
from runectl.user_config import config_path, set_value
from runectl.user_config import get as get_value
from runectl.user_config import load as load_config

app = typer.Typer(
    add_completion=False,
    help="Per-provider preferences (model, thinking) and run-wide defaults (approval, max_cost).",
)


def _split_key(dotted: str) -> tuple[str, str]:
    section, sep, key = dotted.partition(".")
    if not sep or not section or not key:
        raise UsageError(f"expected SECTION.KEY (e.g. anthropic.model) — got {dotted!r}")
    return section, key


@app.command("set")
def config_set(
    dotted_key: str = typer.Argument(..., metavar="SECTION.KEY"),
    value: str = typer.Argument(...),
) -> None:
    """`runectl config set anthropic.model claude-sonnet-5`, `config set anthropic.thinking high`."""
    try:
        section, key = _split_key(dotted_key)
        set_value(section, key, value)
    except UsageError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=6) from exc


@app.command("get")
def config_get(dotted_key: str = typer.Argument(..., metavar="SECTION.KEY")) -> None:
    """Prints the value, or exits 1 with nothing printed if it isn't set."""
    try:
        section, key = _split_key(dotted_key)
        value = get_value(section, key)
    except UsageError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=6) from exc
    if value is None:
        raise typer.Exit(code=1)
    typer.echo(value)


@app.command("list")
def config_list() -> None:
    """One `section.key = value` line per set preference. Empty output if nothing is set."""
    try:
        data = load_config()
    except UsageError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=6) from exc
    for section in sorted(data):
        values = data[section]
        if not isinstance(values, dict):
            continue
        for key in sorted(values):
            typer.echo(f"{section}.{key} = {values[key]}")


@app.command("path")
def config_show_path() -> None:
    """Where config.toml lives (whether or not it exists yet)."""
    typer.echo(str(config_path()))
