"""The model registry is a hand-maintained table, so it gets a table-shaped test.

Nothing here asserts a specific price — those rot, and pinning them would only
mean editing two files instead of one. What it asserts is that every row is
*internally coherent*, because an incoherent row fails at runtime against a live
provider (where it costs money to discover) rather than here.
"""

from __future__ import annotations

from typing import get_args

import pytest

from runectl.providers.registry import (
    MODEL_REGISTRY,
    ProviderName,
    UnknownModelError,
    cheapest_model_for,
    resolve,
)

_PROVIDERS = get_args(ProviderName)


@pytest.mark.parametrize("model_id", sorted(MODEL_REGISTRY))
def test_every_row_is_coherent(model_id: str) -> None:
    model = MODEL_REGISTRY[model_id]

    # The dict key and the row must agree — `resolve()` looks up by key and
    # every caller then reads `.id`, so a mismatch sends the wrong model id
    # over the wire while the trace records the right one.
    assert model.id == model_id
    assert model.provider in _PROVIDERS

    assert model.price_in > 0, "a free model is a typo, not a deal"
    assert model.price_out > 0
    assert model.context_window > 0
    assert 0 < model.cache_read_multiplier <= 1, "a cache read never costs more than a fresh one"

    # `thinking_style` and `max_thinking_level` are only meaningful when the
    # model can think; an inconsistent pair means an adapter will either send a
    # config the API rejects or silently skip one it should have sent.
    if model.supports_thinking:
        assert model.thinking_style != "none"
        assert model.max_thinking_level != "off"
    else:
        assert model.thinking_style == "none"
        assert model.max_thinking_level == "off"
        # "can't be stopped from thinking" is nonsense for a model that never
        # thinks, so the flag must be left at its default.
        assert model.thinking_off_supported


@pytest.mark.parametrize("provider", _PROVIDERS)
def test_every_provider_has_a_utility_model(provider: str) -> None:
    """`cheapest_model_for` is the default `--utility-model`; a provider with no
    rows would make every run on it fail at construction."""
    model = cheapest_model_for(provider)  # type: ignore[arg-type]
    assert model.provider == provider
    assert model is min(
        (m for m in MODEL_REGISTRY.values() if m.provider == provider),
        key=lambda m: m.price_in + m.price_out,
    )


def test_unknown_model_is_an_error_not_a_guess() -> None:
    """D5: providers are never inferred from a string prefix."""
    with pytest.raises(UnknownModelError):
        resolve("claude-sonnet-5-turbo-ultra")
