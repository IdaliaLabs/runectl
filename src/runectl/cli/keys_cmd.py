"""`runectl keys set|list|rm` (D5 §3.3)."""

from __future__ import annotations

import sys
from typing import cast, get_args

import typer

from runectl.providers.keys import list_keys, remove_key, set_key
from runectl.providers.registry import ProviderName

app = typer.Typer(add_completion=False, help="Manage provider API keys.")

_PROVIDERS = get_args(ProviderName)


def _validate_provider(provider: str) -> ProviderName:
    if provider not in _PROVIDERS:
        raise typer.BadParameter(f"provider must be one of {_PROVIDERS}")
    return cast(ProviderName, provider)


@app.command("set")
def set_cmd(provider: str, api_key: str | None = typer.Argument(None)) -> None:
    """Store a provider key. Omit the key argument to read it from stdin, so it
    never appears on the command line (`ps`, shell history)."""
    if api_key is None:
        if sys.stdin.isatty():
            raise typer.BadParameter("provide the key as an argument or pipe it on stdin")
        api_key = sys.stdin.read().strip()
    if not api_key:
        raise typer.BadParameter("empty API key")
    set_key(_validate_provider(provider), api_key)
    typer.echo(f"stored key for {provider}")


@app.command("list")
def list_cmd() -> None:
    for provider, present in list_keys().items():
        typer.echo(f"{provider}: {'set' if present else 'not set'}")


@app.command("rm")
def rm_cmd(provider: str) -> None:
    remove_key(_validate_provider(provider))
    typer.echo(f"removed key for {provider}")
