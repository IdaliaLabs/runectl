"""`runectl keys set|list|rm` (D5 §3.3)."""

from __future__ import annotations

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
def set_cmd(provider: str, api_key: str) -> None:
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
