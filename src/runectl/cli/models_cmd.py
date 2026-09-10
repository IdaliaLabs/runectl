"""`runectl models list` — discover the model registry (Phase 2, D5/D20).

D5 requires `--model` on every `runectl run`, never inferred. This command is
the discoverability answer to that requirement: what's registered, which
providers have a key on this machine, which models support thinking, and
what `runectl config` currently has set as each provider's default.
"""

from __future__ import annotations

import typer

from runectl.providers.keys import list_keys
from runectl.providers.registry import MODEL_REGISTRY
from runectl.user_config import default_model

app = typer.Typer(add_completion=False, help="List registered models, key presence, and thinking support.")


@app.command("list")
def models_list() -> None:
    keys_present = list_keys()
    configured_defaults = {
        provider: default_model(provider) for provider in ("anthropic", "openai", "google")
    }
    for model in sorted(MODEL_REGISTRY.values(), key=lambda m: (m.provider, m.id)):
        has_key = "yes" if keys_present.get(model.provider) else "no"
        thinking = f"yes (max {model.max_thinking_level})" if model.supports_thinking else "no"
        marker = " [configured default]" if configured_defaults.get(model.provider) == model.id else ""
        typer.echo(
            f"{model.id}\tprovider={model.provider}\tkey={has_key}\tthinking={thinking}\t"
            f"ctx={model.context_window}\t${model.price_in:.2f}/${model.price_out:.2f} per 1M{marker}"
        )
