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
    # Cheapest *available*: a retired row is still registered so accounts that
    # kept access can name it explicitly, but it must never be handed to someone
    # as a default they never chose. Google's cheapest row on price alone is
    # gemini-2.5-flash-lite, which 404s for every new account.
    assert not model.retired
    assert model is min(
        (m for m in MODEL_REGISTRY.values() if m.provider == provider and not m.retired),
        key=lambda m: m.price_in + m.price_out,
    )


def test_unknown_model_is_an_error_not_a_guess() -> None:
    """D5: providers are never inferred from a string prefix."""
    with pytest.raises(UnknownModelError):
        resolve("claude-sonnet-5-turbo-ultra")


# The 2026-09-13 live probe of `v1/chat/completions`, one real call per model
# per level with a function tool attached — which is what every runectl step
# sends. Written down because the docs did not predict any of it: `max` is
# accepted by no OpenAI model, `none` is refused by the original `gpt-5`
# family, and reasoning collides with function tools outright on four rows.
_OPENAI_LIVE = {
    # model: (max usable level with tools, can be told not to think, usable at all)
    "gpt-5-nano": ("high", False, True),
    "gpt-5-mini": ("high", False, True),
    "gpt-5": ("high", False, True),
    "gpt-5.1": ("high", True, True),
    "gpt-5.2": ("xhigh", True, True),
    "gpt-5.4": ("off", True, True),
    "gpt-5.4-mini": ("off", True, True),
    "gpt-5.4-nano": ("off", True, True),
    "gpt-5.5": ("off", True, True),
    "gpt-5.6-luna": ("off", True, False),
    "gpt-5.6-terra": ("off", True, False),
    "gpt-5.6-sol": ("off", True, False),
    "gpt-6-astra": ("off", True, False),
}


def test_openai_rows_match_what_the_live_api_accepts() -> None:
    """Every OpenAI row's thinking claim is what a real call proved, not what
    the pricing page implied. Eight rows used to claim `max`, which no OpenAI
    model accepts at all — `resolve_thinking_level` passed it straight through
    and the call 400'd."""
    for model_id, (max_level, off_ok, usable) in _OPENAI_LIVE.items():
        model = MODEL_REGISTRY[model_id]
        assert model.max_thinking_level == max_level, model_id
        assert model.thinking_off_supported == off_ok, model_id
        assert model.retired is not usable, model_id
        assert model.supports_thinking == (max_level != "off"), model_id


def test_no_openai_row_claims_a_level_the_api_rejects() -> None:
    """`max` is in runectl's vocabulary because Anthropic has it. Asking any
    OpenAI model for it is a 400, so no OpenAI row may claim it."""
    for model in MODEL_REGISTRY.values():
        if model.provider == "openai":
            assert model.max_thinking_level != "max", model.id


def test_every_retired_row_says_why() -> None:
    """Two different causes now retire a model — a 404 for new accounts, and a
    tools/reasoning conflict on a model that answers fine otherwise. A single
    hardcoded reason in the CLI was wrong for the second kind."""
    retired = [m for m in MODEL_REGISTRY.values() if m.retired]
    assert len(retired) >= 7
    for model in retired:
        assert model.retired_reason.strip(), model.id
    # and the flag is exactly the presence of a reason
    for model in MODEL_REGISTRY.values():
        assert model.retired == bool(model.retired_reason), model.id
