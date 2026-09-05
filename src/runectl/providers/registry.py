"""Model registry: provider/capability metadata, never string-prefix sniffing (D5, plan §3.2).

``--model`` is required; the provider and every capability (tool support, prompt
caching, context window, price) come from this table. Adding a fourth provider
is a registry entry plus an adapter, not a redesign (D5's "assumptions" note).

Pricing/availability snapshot 2026-09-05 — re-verify before relying on it for
real spend, per this repo's own convention (`CLAUDE.md`: "every research claim
carries the date it was checked; availability and pricing rot fast").
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


MODEL_REGISTRY: dict[str, ModelInfo] = {
    "claude-opus-5": ModelInfo(
        id="claude-opus-5", provider="anthropic", context_window=200_000,
        supports_tools=True, supports_prompt_cache=True, price_in=15.0, price_out=75.0,
    ),
    "claude-sonnet-5": ModelInfo(
        id="claude-sonnet-5", provider="anthropic", context_window=200_000,
        supports_tools=True, supports_prompt_cache=True, price_in=3.0, price_out=15.0,
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
