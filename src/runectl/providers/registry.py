"""Model registry: provider/capability metadata, never string-prefix sniffing (D5, plan §3.2).

``--model`` is required; the provider and every capability (tool support, prompt
caching, context window, price) come from this table. Adding a fourth provider
is a registry entry plus an adapter, not a redesign (D5's "assumptions" note).

Pricing/availability snapshot 2026-09-07, Anthropic rows verified against a live
`client.models.list()` call plus the published price table on that date. The
earlier 2026-09-05 figures were wrong: Opus 5 was listed at 15/75 (actually
5/25), Sonnet 5 at 3/15 (actually 2/10), and both at a 200K context window
(actually 1M). OpenAI and Google rows remain unverified estimates — re-check
them before trusting a cost report for those providers.

Cache multipliers (D18): a cache *write* bills at 1.25x `price_in`, a cache
*read* at 0.10x. Both are provider-standard for Anthropic today and are applied
in `cost.py`, not stored per-model.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

ProviderName = Literal["anthropic", "openai", "google"]


class ModelInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    provider: ProviderName
    context_window: int
    supports_tools: bool
    supports_prompt_cache: bool
    price_in: float  # USD per 1M input tokens
    price_out: float  # USD per 1M output tokens

    # D18 — how much of `price_in` a cached token costs. Anthropic-standard
    # today; if a provider ever differs these become per-model fields.
    cache_write_multiplier: float = 1.25
    cache_read_multiplier: float = 0.10


MODEL_REGISTRY: dict[str, ModelInfo] = {
    "claude-opus-5": ModelInfo(
        id="claude-opus-5", provider="anthropic", context_window=1_000_000,
        supports_tools=True, supports_prompt_cache=True, price_in=5.0, price_out=25.0,
    ),
    "claude-sonnet-5": ModelInfo(
        id="claude-sonnet-5", provider="anthropic", context_window=1_000_000,
        supports_tools=True, supports_prompt_cache=True, price_in=2.0, price_out=10.0,
    ),
    "claude-haiku-4-5-20251001": ModelInfo(
        id="claude-haiku-4-5-20251001", provider="anthropic", context_window=200_000,
        supports_tools=True, supports_prompt_cache=True, price_in=1.0, price_out=5.0,
    ),
    "gpt-5": ModelInfo(
        id="gpt-5", provider="openai", context_window=272_000,
        supports_tools=True, supports_prompt_cache=True, price_in=5.0, price_out=15.0,
    ),
    "gpt-5-mini": ModelInfo(
        id="gpt-5-mini", provider="openai", context_window=272_000,
        supports_tools=True, supports_prompt_cache=True, price_in=0.5, price_out=1.5,
    ),
    "gemini-2.5-pro": ModelInfo(
        id="gemini-2.5-pro", provider="google", context_window=1_000_000,
        supports_tools=True, supports_prompt_cache=True, price_in=1.25, price_out=10.0,
    ),
    "gemini-2.5-flash": ModelInfo(
        id="gemini-2.5-flash", provider="google", context_window=1_000_000,
        supports_tools=True, supports_prompt_cache=False, price_in=0.3, price_out=2.5,
    ),
}


class UnknownModelError(Exception):
    pass


def resolve(model_id: str) -> ModelInfo:
    info = MODEL_REGISTRY.get(model_id)
    if info is None:
        raise UnknownModelError(
            f"unknown model {model_id!r} — not in the model registry "
            "(providers/registry.py). D5: providers are never inferred from a "
            "string prefix, so an unlisted model id is a hard error, not a guess."
        )
    return info


def cheapest_model_for(provider: ProviderName) -> ModelInfo:
    """Default --utility-model: the cheapest model of the same provider as --model."""
    candidates = [m for m in MODEL_REGISTRY.values() if m.provider == provider]
    if not candidates:
        raise UnknownModelError(f"no registered models for provider {provider!r}")
    return min(candidates, key=lambda m: m.price_in + m.price_out)
