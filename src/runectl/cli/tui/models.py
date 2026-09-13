"""How the registry is offered in a dropdown, in one place.

The launcher, the bench screen and the config screen each need the same list and
were each building it differently. With 37 models that stopped being a cosmetic
difference:

- **Price belongs in the label.** Choosing a model during a competition is
  mostly a budget decision, and an id alone does not carry one. Sending someone
  to `m` to look up a price and back again is not a UI.
- **Cheapest first, within provider.** Alphabetical order put `claude-fable-5`
  ($10/$50) at the top of the list and `gpt-5-nano` ($0.05/$0.40) somewhere in
  the middle. The first option in a dropdown is the one people take.
- **Say when a key is missing.** Picking a model whose provider has no key
  configured fails a few seconds into the run instead of at the point of choice.

Textual's `Select` already searches as you type (`type_to_search` defaults to
True), so 37 entries need no custom picker widget.
"""

from __future__ import annotations

from runectl.providers.keys import list_keys
from runectl.providers.registry import MODEL_REGISTRY, ModelInfo


def _total_price(model: ModelInfo) -> float:
    """Sort key: input plus output per 1M. Crude, and right often enough — the
    point is that the cheap tiers surface first, not a spend prediction."""
    return model.price_in + model.price_out


def model_options(*, mark_missing_keys: bool = True) -> list[tuple[str, str]]:
    """`(label, value)` pairs for a `Select`, grouped by provider, cheapest first."""
    present = list_keys() if mark_missing_keys else {}
    options: list[tuple[str, str]] = []
    for model in sorted(MODEL_REGISTRY.values(), key=lambda m: (m.provider, _total_price(m))):
        suffix = ""
        if model.retired:
            suffix = "  · retired"
        elif mark_missing_keys and not present.get(model.provider):
            suffix = "  · no key"
        options.append(
            (
                f"{model.id} · {model.provider} · "
                f"${model.price_in:g}/${model.price_out:g}{suffix}",
                model.id,
            )
        )
    return options


def preferred_model(options: list[tuple[str, str]]) -> str | None:
    """Which option to preselect: the configured Anthropic default if there is
    one, else simply the first — which, given the ordering above, is the
    cheapest model of the first provider rather than an arbitrary one."""
    from runectl.user_config import default_model

    configured = default_model("anthropic")
    if configured and any(value == configured for _, value in options):
        return configured
    return options[0][1] if options else None
