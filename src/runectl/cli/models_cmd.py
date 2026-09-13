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
    # Cheapest first within provider, not alphabetical: with 37 rows, ordering
    # by id buried every cheap tier in the middle of the list, and price is the
    # axis someone reads this command to compare.
    for model in sorted(
        MODEL_REGISTRY.values(), key=lambda m: (m.provider, m.price_in + m.price_out)
    ):
        has_key = "yes" if keys_present.get(model.provider) else "no"
        thinking = f"yes (max {model.max_thinking_level})" if model.supports_thinking else "no"
        marker = " [configured default]" if configured_defaults.get(model.provider) == model.id else ""
        # Loud, not silent: an unmarked row that 404s for most readers is
        # worse than no row. Retired models stay listed because accounts
        # that kept access can still name them explicitly.
        if model.retired:
            marker += f" [RETIRED — {model.retired_reason}]"
        typer.echo(
            f"{model.id}\tprovider={model.provider}\tkey={has_key}\tthinking={thinking}\t"
            f"ctx={model.context_window}\t${model.price_in:.2f}/${model.price_out:.2f} per 1M{marker}"
        )
